import pytest

from app.config import settings
from app.models.xray import ImageCheck, ModelComparison
from app.services import inference, worker
from app.services.body_parts import BODY_PARTS
from tests.readings import reading


def model_output(part_name, *present, boxes=(), confidence="high"):
    part = BODY_PARTS[part_name]
    return part.output(
        checklist={name: name in present for name in part.conditions},
        findings=["Finding"],
        flagged_regions=[],
        devices=[],
        boxes=list(boxes),
        confidence=confidence,
    )


def answer(monkeypatch, check, output=None):
    asked = []

    def ask(images, prompt, schema):
        asked.append((schema, prompt))
        return check if schema is ImageCheck else output

    monkeypatch.setattr(inference, "ask", ask)
    return asked


@pytest.fixture
def image_path(tmp_path):
    path = tmp_path / "scan.png"
    path.write_bytes(b"png")
    return path


@pytest.mark.parametrize(
    ("part", "condition", "label"),
    [
        ("chest", "tb_signs", "Possible TB signs"),
        ("bone_joint", "fracture", "Fracture"),
        ("abdomen", "free_air", "Free air (possible perforation)"),
        ("dental_head", "tooth_decay", "Tooth decay"),
    ],
)
def test_each_body_part_is_read_with_its_own_checklist(monkeypatch, image_path, part, condition, label):
    asked = answer(monkeypatch, ImageCheck(is_xray=True, body_part=part, is_frontal=True), model_output(part, condition))

    result = inference.analyze(image_path)

    assert (result.body_part, result.conditions, result.requires_review) == (part, [label], True)
    assert asked[1][0] is BODY_PARTS[part].output
    assert condition in asked[1][1]


def test_photo_is_not_read(monkeypatch, image_path):
    asked = answer(monkeypatch, ImageCheck(is_xray=False, body_part="other", is_frontal=True))

    with pytest.raises(inference.NotXray):
        inference.analyze(image_path)
    assert len(asked) == 1


@pytest.mark.parametrize("part", ["other", "dental_head"])
def test_unsupported_or_switched_off_body_part_is_not_read(monkeypatch, image_path, part):
    monkeypatch.setattr(settings, "body_parts", ["chest", "bone_joint", "abdomen"])
    answer(monkeypatch, ImageCheck(is_xray=True, body_part=part, is_frontal=True))

    with pytest.raises(inference.UnsupportedBodyPart):
        inference.analyze(image_path)


def test_chest_side_view_is_not_read(monkeypatch, image_path):
    answer(monkeypatch, ImageCheck(is_xray=True, body_part="chest", is_frontal=False))

    with pytest.raises(inference.NotFrontalView):
        inference.analyze(image_path)


def test_bone_side_view_is_read(monkeypatch, image_path):
    answer(monkeypatch, ImageCheck(is_xray=True, body_part="bone_joint", is_frontal=False), model_output("bone_joint"))

    assert inference.analyze(image_path).body_part == "bone_joint"


def test_boxes_are_rounded_and_bad_ones_dropped(monkeypatch, image_path):
    boxes = [
        {"label": "fracture", "box": [100.4, 200, 300, 400.6]},
        {"label": "outside", "box": [900, 0, 1200, 100]},
        {"label": "backwards", "box": [300, 300, 100, 400]},
        {"label": "short", "box": [1, 2, 3]},
    ]
    answer(monkeypatch, ImageCheck(is_xray=True, body_part="bone_joint", is_frontal=True),
           model_output("bone_joint", "fracture", boxes=boxes))

    result = inference.analyze(image_path)

    assert [(box.label, box.box) for box in result.boxes] == [("fracture", [100, 200, 300, 401])]


def test_boxes_are_left_out_when_switched_off(monkeypatch, image_path):
    monkeypatch.setattr(settings, "locate_findings", False)
    asked = answer(monkeypatch, ImageCheck(is_xray=True, body_part="chest", is_frontal=True),
                   model_output("chest", boxes=[{"label": "x", "box": [1, 2, 3, 4]}]))

    assert inference.analyze(image_path).boxes == []
    assert "boxes" not in asked[1][1]


@pytest.mark.parametrize("part", list(BODY_PARTS))
def test_schema_sent_to_model_has_no_references(part):
    schema = inference.inline_schema(BODY_PARTS[part].output)

    assert "$ref" not in str(schema) and "$defs" not in schema
    assert set(schema["properties"]["checklist"]["properties"]) == set(BODY_PARTS[part].conditions)


def test_comparison_gets_both_images_oldest_first(monkeypatch, tmp_path):
    (tmp_path / "old.png").write_bytes(b"old")
    (tmp_path / "new.png").write_bytes(b"new")
    sent = []

    def ask(images, prompt, schema):
        sent.append(images)
        return ModelComparison(change="worse", summary="Larger opacity")

    monkeypatch.setattr(inference, "ask", ask)

    result = inference.compare(tmp_path / "old.png", tmp_path / "new.png")

    assert result.change == "worse"
    assert sent == [[inference.encode(tmp_path / "old.png"), inference.encode(tmp_path / "new.png")]]


@pytest.mark.parametrize("error", [inference.ModelUnavailable(), inference.InvalidModelOutput(), inference.InferenceTimeout()])
def test_failed_comparison_is_skipped(monkeypatch, tmp_path, error):
    (tmp_path / "a.png").write_bytes(b"a")

    def ask(images, prompt, schema):
        raise error

    monkeypatch.setattr(inference, "ask", ask)

    assert inference.compare(tmp_path / "a.png", tmp_path / "a.png") is None


def submit_for(client, xray_image, patient="P-001"):
    response = client.post(
        "/api/xray/analyze", json={"image": xray_image, "facility_id": "FAC-001", "patient_ref": patient}
    )
    return response.json()["scan_id"]


def test_new_scan_is_compared_with_patients_previous_scan(client, db, xray_image, monkeypatch):
    monkeypatch.setattr(inference, "analyze", lambda path: reading())
    monkeypatch.setattr(inference, "compare", lambda old, new: ModelComparison(change="worse", summary="New opacity"))
    first = submit_for(client, xray_image)
    other_patient = submit_for(client, xray_image, patient="P-002")
    worker.process_next(db)
    worker.process_next(db)
    second = submit_for(client, xray_image)

    worker.process_next(db)

    assert client.get(f"/api/xray/results/{first}").json()["comparison"] is None
    assert client.get(f"/api/xray/results/{other_patient}").json()["comparison"] is None
    body = client.get(f"/api/xray/results/{second}").json()
    assert body["comparison"] == {"change": "worse", "summary": "New opacity", "previous_scan_id": first}
    assert body["requires_review"] is True
    assert body["conditions"] == []


def test_scans_of_different_body_parts_are_not_compared(client, db, xray_image, monkeypatch):
    readings = iter([reading("fracture", body_part="bone_joint"), reading()])
    monkeypatch.setattr(inference, "analyze", lambda path: next(readings))
    monkeypatch.setattr(inference, "compare", lambda old, new: pytest.fail("should not compare"))
    submit_for(client, xray_image)
    worker.process_next(db)
    second = submit_for(client, xray_image)

    worker.process_next(db)

    assert client.get(f"/api/xray/results/{second}").json()["comparison"] is None


def test_comparison_can_be_switched_off(client, db, xray_image, monkeypatch):
    monkeypatch.setattr(settings, "compare_with_previous", False)
    monkeypatch.setattr(inference, "analyze", lambda path: reading())
    monkeypatch.setattr(inference, "compare", lambda old, new: pytest.fail("should not compare"))
    submit_for(client, xray_image)
    worker.process_next(db)
    second = submit_for(client, xray_image)

    worker.process_next(db)

    assert client.get(f"/api/xray/results/{second}").json()["status"] == "complete"


def test_result_includes_body_part_and_boxes(client, db, xray_image, monkeypatch):
    found = reading("fracture", body_part="bone_joint", boxes=[{"label": "fracture", "box": [10, 20, 30, 40]}])
    monkeypatch.setattr(inference, "analyze", lambda path: found)
    scan_id = submit_for(client, xray_image)

    worker.process_next(db)

    body = client.get(f"/api/xray/results/{scan_id}").json()
    assert body["body_part"] == "bone_joint"
    assert body["conditions"] == ["Fracture"]
    assert body["boxes"] == [{"label": "fracture", "box": [10, 20, 30, 40]}]
