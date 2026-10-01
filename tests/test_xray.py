import base64
from io import BytesIO

import pytest
from PIL import Image

from app.models.xray import ModelFindings
from app.services import images, inference, worker

NORMAL = ModelFindings(findings=["Clear lung fields"], flagged_regions=[], abnormal=False, confidence="high")
ABNORMAL = ModelFindings(
    findings=["Opacity in the right upper zone"],
    flagged_regions=["right upper zone"],
    abnormal=True,
    confidence="high",
)
UNSURE = ModelFindings(findings=["Clear lung fields"], flagged_regions=[], abnormal=False, confidence="low")


def analyze_returns(monkeypatch, output):
    monkeypatch.setattr(inference, "analyze", lambda path: output)


def analyze_raises(monkeypatch, error):
    def _raise(path):
        raise error

    monkeypatch.setattr(inference, "analyze", _raise)


def test_submit_returns_pending_scan(client, submit):
    response = submit()

    assert response.status_code == 202
    scan_id = response.json()["scan_id"]
    assert response.json()["status"] == "pending"
    assert client.get(f"/api/xray/results/{scan_id}").json() == {
        "scan_id": scan_id,
        "status": "pending",
        "findings": [],
        "flagged_regions": [],
        "confidence": None,
        "requires_review": True,
        "synced_to_dhis2": False,
        "error": None,
    }


def test_completed_scan_returns_findings(client, db, submit, monkeypatch):
    analyze_returns(monkeypatch, ABNORMAL)
    scan_id = submit().json()["scan_id"]

    assert worker.process_next(db) is True

    body = client.get(f"/api/xray/results/{scan_id}").json()
    assert body["status"] == "complete"
    assert body["findings"] == ["Opacity in the right upper zone"]
    assert body["flagged_regions"] == ["right upper zone"]
    assert body["confidence"] == "high"
    assert body["requires_review"] is True
    assert body["synced_to_dhis2"] is False


@pytest.mark.parametrize(
    ("output", "requires_review"),
    [(NORMAL, False), (ABNORMAL, True), (UNSURE, True)],
)
def test_requires_review(client, db, submit, monkeypatch, output, requires_review):
    analyze_returns(monkeypatch, output)
    scan_id = submit().json()["scan_id"]

    worker.process_next(db)

    assert client.get(f"/api/xray/results/{scan_id}").json()["requires_review"] is requires_review


def test_completed_scan_is_queued_for_sync(db, submit, monkeypatch):
    analyze_returns(monkeypatch, NORMAL)
    scan_id = submit().json()["scan_id"]

    worker.process_next(db)

    row = db.execute("SELECT status, attempts FROM sync_queue WHERE result_id = ?", (scan_id,)).fetchone()
    assert (row["status"], row["attempts"]) == ("queued", 0)


def test_scans_are_processed_oldest_first(client, db, submit, monkeypatch):
    analyze_returns(monkeypatch, NORMAL)
    first = submit().json()["scan_id"]
    second = submit().json()["scan_id"]

    worker.process_next(db)

    assert client.get(f"/api/xray/results/{first}").json()["status"] == "complete"
    assert client.get(f"/api/xray/results/{second}").json()["status"] == "pending"


def test_process_next_with_empty_queue(db):
    assert worker.process_next(db) is False


@pytest.mark.parametrize(
    "image",
    ["not base64!!", base64.b64encode(b"plain text, not an image").decode()],
)
def test_unreadable_image_returns_422(submit, db, image):
    response = submit(image)

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_image"
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0


def test_data_url_image_is_accepted(submit, xray_image):
    assert submit(f"data:image/png;base64,{xray_image}").status_code == 202


@pytest.mark.parametrize("image_format", ["PNG", "JPEG", "BMP", "TIFF", "WEBP", "GIF"])
def test_common_image_formats_are_accepted(submit, image_format):
    buffer = BytesIO()
    Image.new("RGB", (64, 64), (128, 128, 128)).save(buffer, image_format)

    assert submit(base64.b64encode(buffer.getvalue()).decode()).status_code == 202


def test_16_bit_image_keeps_its_contrast():
    source = Image.new("I;16", (2, 1))
    source.putpixel((0, 0), 1000)
    source.putpixel((1, 0), 3000)
    buffer = BytesIO()
    source.save(buffer, "PNG")

    image = images.decode(base64.b64encode(buffer.getvalue()).decode())

    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert image.getpixel((1, 0)) == (255, 255, 255)


def test_missing_field_returns_422(client, xray_image):
    response = client.post("/api/xray/analyze", json={"image": xray_image})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_model_unavailable_returns_503(submit, monkeypatch):
    monkeypatch.setattr(inference, "model_ready", lambda: False)

    response = submit()

    assert response.status_code == 503
    assert response.json() == {"error": "model_unavailable"}


def test_inference_timeout_returns_504(client, db, submit, monkeypatch):
    analyze_raises(monkeypatch, inference.InferenceTimeout())
    scan_id = submit().json()["scan_id"]

    worker.process_next(db)

    response = client.get(f"/api/xray/results/{scan_id}")
    assert response.status_code == 504
    assert response.json() == {"error": "inference_timeout"}


def test_invalid_model_output_fails_scan(client, db, submit, monkeypatch):
    analyze_raises(monkeypatch, inference.InvalidModelOutput())
    scan_id = submit().json()["scan_id"]

    worker.process_next(db)

    body = client.get(f"/api/xray/results/{scan_id}").json()
    assert (body["status"], body["error"]) == ("failed", "invalid_model_output")


def test_scan_stays_pending_when_model_drops(client, db, submit, monkeypatch):
    analyze_raises(monkeypatch, inference.ModelUnavailable())
    scan_id = submit().json()["scan_id"]

    with pytest.raises(inference.ModelUnavailable):
        worker.process_next(db)

    assert client.get(f"/api/xray/results/{scan_id}").json()["status"] == "pending"


def test_interrupted_scan_is_recovered(client, db, submit, monkeypatch):
    scan_id = submit().json()["scan_id"]
    worker.set_status(db, scan_id, "processing")

    worker.recover(db)
    analyze_returns(monkeypatch, NORMAL)
    worker.process_next(db)

    assert client.get(f"/api/xray/results/{scan_id}").json()["status"] == "complete"


def test_unknown_scan_returns_404(client):
    response = client.get("/api/xray/results/missing")

    assert response.status_code == 404
    assert response.json() == {"error": "scan_not_found"}
