import json
import logging
import sqlite3
import threading
from pathlib import Path

from app.config import settings
from app.db.database import connect
from app.models.xray import Comparison, Reading
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


def previous_scan(conn: sqlite3.Connection, scan: sqlite3.Row, body_part: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT s.id, s.image_path FROM scans s JOIN results r ON r.scan_id = s.id"
        " WHERE s.facility_id = ? AND s.patient_ref = ? AND s.id != ? AND r.body_part = ?"
        " ORDER BY s.created_at DESC, s.rowid DESC LIMIT 1",
        (scan["facility_id"], scan["patient_ref"], scan["id"], body_part),
    ).fetchone()


def comparison_for(conn: sqlite3.Connection, scan: sqlite3.Row, reading: Reading) -> Comparison | None:
    if not settings.compare_with_previous:
        return None
    previous = previous_scan(conn, scan, reading.body_part)
    if previous is None:
        return None
    compared = inference.compare(Path(previous["image_path"]), Path(scan["image_path"]))
    if compared is None:
        return None
    return Comparison(previous_scan_id=previous["id"], **compared.model_dump())


def process_next(conn: sqlite3.Connection) -> bool:
    scan = conn.execute(
        "SELECT id, facility_id, patient_ref, image_path FROM scans WHERE status = 'pending'"
        " ORDER BY created_at, rowid LIMIT 1"
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
    except inference.NotXray:
        set_status(conn, scan_id, "failed", "not_xray")
        return True
    except inference.UnsupportedBodyPart:
        set_status(conn, scan_id, "failed", "unsupported_body_part")
        return True
    except inference.NotFrontalView:
        set_status(conn, scan_id, "failed", "not_frontal_view")
        return True
    except inference.InvalidModelOutput:
        set_status(conn, scan_id, "failed", "invalid_model_output")
        return True
    except Exception:
        logger.exception("scan %s failed", scan_id)
        set_status(conn, scan_id, "failed", "internal_error")
        return True

    comparison = comparison_for(conn, scan, output)
    requires_review = output.requires_review or (
        comparison is not None and comparison.change in ("worse", "new_finding")
    )
    with conn:
        conn.execute(
            "INSERT INTO results"
            " (scan_id, findings, flagged_regions, body_part, conditions, devices, boxes, comparison,"
            " confidence, requires_review)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                scan_id,
                json.dumps(output.findings),
                json.dumps(output.flagged_regions),
                output.body_part,
                json.dumps(output.conditions),
                json.dumps(output.devices),
                json.dumps([box.model_dump() for box in output.boxes]),
                comparison.model_dump_json() if comparison else None,
                output.confidence,
                requires_review,
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
