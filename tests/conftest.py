import base64
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import settings
from app.db.database import connect
from app.main import app
from app.services import inference, worker


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "database_path", tmp_path / "test.db")
    monkeypatch.setattr(settings, "image_dir", tmp_path / "images")
    monkeypatch.setattr(worker, "start", lambda: None)
    monkeypatch.setattr(inference, "model_ready", lambda: True)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def db(client):
    conn = connect()
    yield conn
    conn.close()


@pytest.fixture
def xray_image():
    buffer = BytesIO()
    Image.new("L", (64, 64), 128).save(buffer, "PNG")
    return base64.b64encode(buffer.getvalue()).decode()


@pytest.fixture
def submit(client, xray_image):
    def _submit(image=xray_image):
        return client.post(
            "/api/xray/analyze",
            json={"image": image, "facility_id": "FAC-001", "patient_ref": "P-001"},
        )

    return _submit
