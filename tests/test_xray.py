import base64
from io import BytesIO

import numpy as np
import pytest
from PIL import Image
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.encaps import encapsulate
from pydicom.uid import ExplicitVRLittleEndian, JPEG2000Lossless, generate_uid

from app.api.errors import MESSAGES
from app.services import images, inference, worker
from tests.readings import reading

NORMAL = reading()
ABNORMAL = reading(
    "tb_signs",
    "pleural_effusion",
    findings=["Opacity in the right upper zone", "Blunted left costophrenic angle"],
    regions=["right upper zone"],
    devices=["central venous line"],
)
UNSURE = reading(confidence="low")


def dicom(pixels, photometric="MONOCHROME2", view="PA", transfer_syntax=ExplicitVRLittleEndian, **extra):
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.1.1"
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = transfer_syntax
    dataset = Dataset()
    dataset.file_meta = meta
    dataset.SOPClassUID = meta.MediaStorageSOPClassUID
    dataset.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    dataset.Modality = "DX"
    dataset.ViewPosition = view
    dataset.Rows, dataset.Columns = pixels.shape
    dataset.SamplesPerPixel = 1
    dataset.PhotometricInterpretation = photometric
    dataset.BitsAllocated = 16
    dataset.BitsStored = 12
    dataset.HighBit = 11
    dataset.PixelRepresentation = 0
    if transfer_syntax.is_compressed:
        dataset.PixelData = encapsulate([b"not a jpeg 2000 frame"])
    else:
        dataset.PixelData = pixels.astype(np.uint16).tobytes()
    for name, value in extra.items():
        setattr(dataset, name, value)
    buffer = BytesIO()
    dataset.save_as(buffer, enforce_file_format=True)
    return base64.b64encode(buffer.getvalue()).decode()


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
        "facility_id": "FAC-001",
        "patient_ref": "P-001",
        "status": "pending",
        "body_part": None,
        "conditions": [],
        "findings": [],
        "flagged_regions": [],
        "devices": [],
        "boxes": [],
        "comparison": None,
        "confidence": None,
        "requires_review": True,
        "synced_to_dhis2": False,
        "error": None,
        "message": None,
    }


def test_completed_scan_returns_findings(client, db, submit, monkeypatch):
    analyze_returns(monkeypatch, ABNORMAL)
    scan_id = submit().json()["scan_id"]

    assert worker.process_next(db) is True

    body = client.get(f"/api/xray/results/{scan_id}").json()
    assert body["status"] == "complete"
    assert (body["facility_id"], body["patient_ref"]) == ("FAC-001", "P-001")
    assert body["conditions"] == ["Possible TB signs", "Fluid around the lung (pleural effusion)"]
    assert body["findings"] == ["Opacity in the right upper zone", "Blunted left costophrenic angle"]
    assert body["flagged_regions"] == ["right upper zone"]
    assert body["devices"] == ["central venous line"]
    assert body["confidence"] == "high"
    assert body["requires_review"] is True
    assert body["synced_to_dhis2"] is False


@pytest.mark.parametrize(
    ("output", "requires_review"),
    [
        (NORMAL, False),
        (ABNORMAL, True),
        (UNSURE, True),
        (reading("other_abnormality"), True),
        (reading(devices=["pacemaker"]), False),
        (reading("device_misplaced", devices=["endotracheal tube"]), True),
    ],
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


def test_colour_photo_returns_422(submit):
    buffer = BytesIO()
    Image.new("RGB", (64, 64), (40, 140, 50)).save(buffer, "PNG")

    response = submit(base64.b64encode(buffer.getvalue()).decode())

    assert response.status_code == 422
    assert response.json() == {
        "error": "invalid_image",
        "message": "This is a colour photo. Please upload the X-ray image itself.",
    }


def test_rotated_phone_photo_is_turned_upright():
    source = Image.new("L", (4, 2), 0)
    exif = source.getexif()
    exif[0x0112] = 6
    buffer = BytesIO()
    source.save(buffer, "JPEG", exif=exif)

    image = images.decode(base64.b64encode(buffer.getvalue()).decode())

    assert image.size == (2, 4)


def test_tinted_greyscale_is_accepted(submit):
    buffer = BytesIO()
    Image.new("RGB", (64, 64), (120, 128, 140)).save(buffer, "JPEG")

    assert submit(base64.b64encode(buffer.getvalue()).decode()).status_code == 202


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (inference.NotXray(), "not_xray"),
        (inference.UnsupportedBodyPart(), "unsupported_body_part"),
        (inference.NotFrontalView(), "not_frontal_view"),
    ],
)
def test_rejected_image_fails_scan_with_message(client, db, submit, monkeypatch, error, code):
    analyze_raises(monkeypatch, error)
    scan_id = submit().json()["scan_id"]

    worker.process_next(db)

    body = client.get(f"/api/xray/results/{scan_id}").json()
    assert (body["status"], body["error"]) == ("failed", code)
    assert body["message"] == MESSAGES[code]


def test_dicom_is_accepted(submit):
    assert submit(dicom(np.full((64, 64), 2000))).status_code == 202


def test_dicom_keeps_its_contrast():
    image = images.decode(dicom(np.array([[1000, 3000]])))

    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert image.getpixel((1, 0)) == (255, 255, 255)


def test_inverted_dicom_is_shown_bones_white():
    image = images.decode(dicom(np.array([[1000, 3000]]), photometric="MONOCHROME1"))

    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.getpixel((1, 0)) == (0, 0, 0)


def test_dicom_window_is_applied():
    image = images.decode(dicom(np.array([[0, 1500, 4000]]), WindowCenter=1500, WindowWidth=1000))

    assert image.getpixel((0, 0)) == (0, 0, 0)
    assert image.getpixel((2, 0)) == (255, 255, 255)


@pytest.mark.parametrize("view", ["LL", "RL"])
def test_side_view_dicom_returns_422(submit, db, view):
    response = submit(dicom(np.full((64, 64), 2000), view=view))

    assert response.status_code == 422
    assert response.json() == {
        "error": "not_frontal_view",
        "message": "This looks like a side view. Please upload the front (PA) view of the chest.",
    }
    assert db.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 0


@pytest.mark.parametrize(("view", "body_part"), [("LL", "KNEE"), ("LATERAL", "HAND")])
def test_side_view_dicom_of_bone_is_accepted(submit, view, body_part):
    assert submit(dicom(np.full((64, 64), 2000), view=view, BodyPartExamined=body_part)).status_code == 202


def test_side_view_dicom_marked_chest_returns_422(submit):
    response = submit(dicom(np.full((64, 64), 2000), view="LL", BodyPartExamined="CHEST"))

    assert response.json()["error"] == "not_frontal_view"


def test_batch_queues_each_scan(client, db, xray_image):
    scans = [
        {"image": xray_image, "facility_id": "FAC-001", "patient_ref": "P-001"},
        {"image": "not an image", "facility_id": "FAC-001", "patient_ref": "P-002"},
        {"image": xray_image, "facility_id": "FAC-001", "patient_ref": "P-003"},
    ]

    response = client.post("/api/xray/analyze/batch", json={"scans": scans})

    assert response.status_code == 202
    items = response.json()["scans"]
    assert [(item["index"], item["status"], item["error"]) for item in items] == [
        (0, "pending", None),
        (1, None, "invalid_image"),
        (2, "pending", None),
    ]
    assert items[1]["message"]
    assert [row["patient_ref"] for row in db.execute("SELECT patient_ref FROM scans ORDER BY rowid")] == ["P-001", "P-003"]
    assert client.get(f"/api/xray/results/{items[2]['scan_id']}").json()["patient_ref"] == "P-003"


@pytest.mark.parametrize("count", [0, 21])
def test_batch_size_is_limited(client, xray_image, count):
    scans = [{"image": xray_image, "facility_id": "FAC-001", "patient_ref": f"P-{n}"} for n in range(count)]

    response = client.post("/api/xray/analyze/batch", json={"scans": scans})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_batch_returns_503_when_model_is_down(client, xray_image, monkeypatch):
    monkeypatch.setattr(inference, "model_ready", lambda: False)
    scans = [{"image": xray_image, "facility_id": "FAC-001", "patient_ref": "P-001"}]

    assert client.post("/api/xray/analyze/batch", json={"scans": scans}).status_code == 503


def test_unreadable_dicom_returns_422(submit):
    response = submit(dicom(np.full((8, 8), 2000), transfer_syntax=JPEG2000Lossless))

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_image"
    assert "DICOM" in response.json()["message"]


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
    assert response.json()["message"] == "Some required information is missing or wrong."
    assert "facility_id" in response.json()["detail"]


def test_model_unavailable_returns_503(submit, monkeypatch):
    monkeypatch.setattr(inference, "model_ready", lambda: False)

    response = submit()

    assert response.status_code == 503
    assert response.json() == {
        "error": "model_unavailable",
        "message": "The X-ray reader is not running. Please try again in a few minutes.",
    }


def test_inference_timeout_returns_504(client, db, submit, monkeypatch):
    analyze_raises(monkeypatch, inference.InferenceTimeout())
    scan_id = submit().json()["scan_id"]

    worker.process_next(db)

    response = client.get(f"/api/xray/results/{scan_id}")
    assert response.status_code == 504
    assert response.json() == {
        "error": "inference_timeout",
        "message": "Reading the X-ray took too long. Please submit it again.",
    }


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
    assert response.json() == {"error": "scan_not_found", "message": "No scan was found with this ID."}
