import sqlite3

from app.config import settings
from app.services import inference


def test_health_ok_when_model_installed(client, monkeypatch):
    monkeypatch.setattr(settings, "ollama_model", "medgemma")
    monkeypatch.setattr(inference, "installed_models", lambda: ["medgemma:4b"])

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "ollama_reachable": True,
        "model_available": True,
        "model": "medgemma",
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


def test_startup_creates_schema(client):
    conn = sqlite3.connect(settings.database_path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()

    assert {"scans", "results", "sync_queue"} <= tables
