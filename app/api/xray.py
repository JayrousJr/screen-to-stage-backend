import json
import sqlite3
from uuid import uuid4

from fastapi import APIRouter, Depends

from app.api.errors import MESSAGES, ApiError
from app.db.database import get_db
from app.models.xray import AnalyzeRequest, AnalyzeResponse, ResultResponse
from app.services import images, inference, worker

router = APIRouter(prefix="/xray")


@router.post("/analyze", response_model=AnalyzeResponse, status_code=202)
def analyze(request: AnalyzeRequest, db: sqlite3.Connection = Depends(get_db)) -> AnalyzeResponse:
    if not inference.model_ready():
        raise ApiError(503, "model_unavailable")

    scan_id = uuid4().hex
    try:
        image_path = images.store(request.image, scan_id)
    except images.InvalidImage as exc:
        raise ApiError(422, "invalid_image", str(exc))

    with db:
        db.execute(
            "INSERT INTO scans (id, facility_id, patient_ref, image_path) VALUES (?, ?, ?, ?)",
            (scan_id, request.facility_id, request.patient_ref, str(image_path)),
        )
    worker.wake()
    return AnalyzeResponse(scan_id=scan_id, status="pending")


@router.get("/results/{scan_id}", response_model=ResultResponse)
def result(scan_id: str, db: sqlite3.Connection = Depends(get_db)) -> ResultResponse:
    row = db.execute(
        "SELECT s.status, s.error, r.findings, r.flagged_regions, r.confidence,"
        " r.requires_review, q.status AS sync_status"
        " FROM scans s"
        " LEFT JOIN results r ON r.scan_id = s.id"
        " LEFT JOIN sync_queue q ON q.result_id = s.id"
        " WHERE s.id = ?",
        (scan_id,),
    ).fetchone()
    if row is None:
        raise ApiError(404, "scan_not_found")
    if row["error"] == "inference_timeout":
        raise ApiError(504, "inference_timeout")
    if row["status"] != "complete":
        return ResultResponse(
            scan_id=scan_id,
            status=row["status"],
            error=row["error"],
            message=MESSAGES.get(row["error"]),
        )

    return ResultResponse(
        scan_id=scan_id,
        status="complete",
        findings=json.loads(row["findings"]),
        flagged_regions=json.loads(row["flagged_regions"]),
        confidence=row["confidence"],
        requires_review=bool(row["requires_review"]),
        synced_to_dhis2=row["sync_status"] == "synced",
    )
