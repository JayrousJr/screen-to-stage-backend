import sqlite3

from app.db.database import init_db

from app.config import settings
from app.services import inference


def test_health_ok_when_model_installed(client, monkeypatch):
    monkeypatch.setattr(settings, "ollama_model", "medgemma1.5:4b-it-bf16")
    monkeypatch.setattr(inference, "installed_models", lambda: ["medgemma1.5:4b-it-bf16"])

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "ollama_reachable": True,
        "model_available": True,
        "model": "medgemma1.5:4b-it-bf16",
    }


def test_health_degraded_when_model_missing(client, monkeypatch):
    monkeypatch.setattr(inference, "installed_models", lambda: ["llama3:latest"])

    body = client.get("/api/health").json()

    assert body["status"] == "degraded"
    assert body["ollama_reachable"] is True
    assert body["model_available"] is False


def test_health_degraded_when_ollama_unreachable(client, monkeypatch):
    monkeypatch.setattr(inference, "installed_models", lambda: None)

    body = client.get("/api/health").json()

    assert body["status"] == "degraded"
    assert body["ollama_reachable"] is False
    assert body["model_available"] is False


def test_older_database_gets_new_columns(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "old.db")
    monkeypatch.setattr(settings, "image_dir", tmp_path / "images")
    conn = sqlite3.connect(settings.database_path)
    conn.execute(
        "CREATE TABLE results (scan_id TEXT PRIMARY KEY, findings TEXT NOT NULL, flagged_regions TEXT NOT NULL,"
        " confidence TEXT NOT NULL, requires_review INTEGER NOT NULL)"
    )
    conn.execute("INSERT INTO results VALUES ('old', '[]', '[]', 'high', 0)")
    conn.commit()
    conn.close()

    init_db()

    conn = sqlite3.connect(settings.database_path)
    row = conn.execute(
        "SELECT body_part, conditions, devices, boxes, comparison FROM results WHERE scan_id = 'old'"
    ).fetchone()
    conn.close()
    assert row == ("chest", "[]", "[]", "[]", None)


def test_startup_creates_schema(client):
    conn = sqlite3.connect(settings.database_path)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    conn.close()

    assert {"scans", "results", "sync_queue"} <= tables
