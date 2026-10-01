import json
import logging
import sqlite3
import threading
from pathlib import Path

from app.db.database import connect
from app.services import inference

POLL_SECONDS = 5.0
RETRY_SECONDS = 10.0

logger = logging.getLogger(__name__)

_wake = threading.Event()
_stop = threading.Event()
_thread: threading.Thread | None = None


def recover(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("UPDATE scans SET status = 'pending' WHERE status = 'processing'")


def set_status(conn: sqlite3.Connection, scan_id: str, status: str, error: str | None = None) -> None:
    with conn:
        conn.execute("UPDATE scans SET status = ?, error = ? WHERE id = ?", (status, error, scan_id))


def process_next(conn: sqlite3.Connection) -> bool:
    scan = conn.execute(
        "SELECT id, image_path FROM scans WHERE status = 'pending' ORDER BY created_at, rowid LIMIT 1"
    ).fetchone()
    if scan is None:
        return False
    scan_id = scan["id"]
    set_status(conn, scan_id, "processing")

    try:
        output = inference.analyze(Path(scan["image_path"]))
    except inference.ModelUnavailable:
        set_status(conn, scan_id, "pending")
        raise
    except inference.InferenceTimeout:
        set_status(conn, scan_id, "failed", "inference_timeout")
        return True
    except inference.InvalidModelOutput:
        set_status(conn, scan_id, "failed", "invalid_model_output")
        return True
    except Exception:
        logger.exception("scan %s failed", scan_id)
        set_status(conn, scan_id, "failed", "internal_error")
        return True

    with conn:
        conn.execute(
            "INSERT INTO results (scan_id, findings, flagged_regions, confidence, requires_review)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                scan_id,
                json.dumps(output.findings),
                json.dumps(output.flagged_regions),
                output.confidence,
                output.requires_review,
            ),
        )
        conn.execute("INSERT INTO sync_queue (result_id) VALUES (?)", (scan_id,))
        conn.execute("UPDATE scans SET status = 'complete' WHERE id = ?", (scan_id,))
    return True


def run() -> None:
    conn = connect()
    try:
        recover(conn)
        while not _stop.is_set():
            try:
                worked = process_next(conn)
            except inference.ModelUnavailable:
                _stop.wait(RETRY_SECONDS)
                continue
            if not worked:
                _wake.wait(POLL_SECONDS)
                _wake.clear()
    finally:
        conn.close()


def wake() -> None:
    _wake.set()


def start() -> None:
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=run, name="scan-worker", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    _wake.set()
