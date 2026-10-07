import argparse
import base64
from pathlib import Path

import pytest

from app.models.xray import ModelFindings
from app.services import inference, worker
from scripts import evaluate

TB = ModelFindings(findings=["Cavity"], flagged_regions=["right upper zone"], abnormal=True, confidence="high")
CLEAR = ModelFindings(findings=["Clear lung fields"], flagged_regions=[], abnormal=False, confidence="high")


@pytest.mark.parametrize(
    ("name", "expected"),
    [("CHNCXR_0001_0.png", "normal"), ("MCUCXR_0104_1.png", "tb"), ("scan.png", None)],
)
def test_label_comes_from_file_name(name, expected):
    assert evaluate.label(Path(name)) == expected


def test_wilson_range_contains_rate():
    low, high = evaluate.wilson(45, 50)

    assert low < 0.9 < high
    assert 0.75 < low and high < 0.97


def test_summary_counts_screening_and_checks():
    rows = [
        {"expected": "tb", "outcome": "read", "requires_review": "True", "confidence": "high", "seconds": "10"},
        {"expected": "tb", "outcome": "read", "requires_review": "False", "confidence": "high", "seconds": "12"},
        {"expected": "tb", "outcome": "not_chest_xray", "requires_review": "", "confidence": "", "seconds": ""},
        {"expected": "normal", "outcome": "read", "requires_review": "False", "confidence": "high", "seconds": "9"},
        {"expected": "not_chest", "outcome": "invalid_image", "requires_review": "", "confidence": "", "seconds": ""},
        {"expected": "side_view", "outcome": "read", "requires_review": "True", "confidence": "low", "seconds": "11"},
    ]

    lines = {line.split(":")[0].strip(): line for line in evaluate.summarise(rows).splitlines()}

    assert "(1/2," in lines["TB cases flagged for review (sensitivity)"]
    assert "(1/1," in lines["Normal cases not flagged (specificity)"]
    assert "(1/3," in lines["TB cases flagged, counting rejects as missed"]
    assert "(1/4," in lines["Real chest X-rays wrongly rejected"]
    assert "(1/1," in lines["Non-chest images rejected"]
    assert "(0/1," in lines["Side views rejected for any reason"]
    assert "not_chest_xray 1" in lines["Why real chest X-rays were rejected"]


def test_images_are_run_through_the_api(client, db, tmp_path, xray_image, monkeypatch):
    folder = tmp_path / "shenzhen"
    folder.mkdir()
    (folder / "CHNCXR_0001_0.png").write_bytes(base64.b64decode(xray_image))
    (folder / "CHNCXR_0002_1.png").write_bytes(base64.b64decode(xray_image))
    (folder / "notes.txt").write_text("not an image")
    (folder / "masks").mkdir()
    (folder / "masks" / "CHNCXR_0001_0.png").write_bytes(base64.b64decode(xray_image))
    readings = iter([CLEAR, TB])
    monkeypatch.setattr(inference, "analyze", lambda path: next(readings))
    monkeypatch.setattr(evaluate.time, "sleep", lambda seconds: worker.process_next(db))

    args = argparse.Namespace(labelled=[folder], not_chest=[], side_view=[])
    results = [(expected, evaluate.run_one(client, path, 0)) for _, path, expected in evaluate.cases(args)]

    assert [(expected, r["outcome"], r["requires_review"]) for expected, r in results] == [
        ("normal", "read", False),
        ("tb", "read", True),
    ]
