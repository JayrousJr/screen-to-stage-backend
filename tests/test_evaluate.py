import argparse
import base64
from pathlib import Path

import pytest

from app.services import inference, worker
from scripts import evaluate
from tests.readings import reading

TB = reading("tb_signs", findings=["Cavity"], regions=["right upper zone"])
CLEAR = reading()


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
    assert "(1/1," in lines["Images that should be rejected, rejected"]
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
    args = argparse.Namespace(labelled=[folder], normal=[], tb=[], sick=[], not_chest=[], side_view=[], sample=None)
    found = evaluate.cases(args)
    readings = iter(TB if expected == "tb" else CLEAR for _, _, expected in found)
    monkeypatch.setattr(inference, "analyze", lambda path: next(readings))
    monkeypatch.setattr(evaluate.time, "sleep", lambda seconds: worker.process_next(db))

    results = {path.name: evaluate.run_one(client, path, 0) for _, path, _ in found}

    assert {name: (r["outcome"], r["requires_review"]) for name, r in results.items()} == {
        "CHNCXR_0001_0.png": ("read", False),
        "CHNCXR_0002_1.png": ("read", True),
    }


def test_folders_give_the_answer_and_sample_is_balanced(tmp_path):
    for name, count in (("health", 30), ("tb", 10), ("sick", 30)):
        (tmp_path / name).mkdir()
        for number in range(count):
            (tmp_path / name / f"{name}{number:04}.png").write_bytes(b"png")
    args = argparse.Namespace(
        labelled=[], normal=[tmp_path / "health"], tb=[tmp_path / "tb"], sick=[tmp_path / "sick"],
        not_chest=[], side_view=[], sample=10,
    )

    found = evaluate.cases(args)

    assert sorted(expected for _, _, expected in found) == ["normal"] * 10 + ["sick"] * 10 + ["tb"] * 10
    assert all(path.parent.name == {"normal": "health", "tb": "tb", "sick": "sick"}[expected] for _, path, expected in found)
    assert found == evaluate.cases(args)
