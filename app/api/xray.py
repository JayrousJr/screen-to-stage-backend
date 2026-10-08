import json
import sqlite3
from uuid import uuid4

from fastapi import APIRouter, Depends

from app.api.errors import MESSAGES, ApiError
from app.db.database import get_db
from app.models.xray import (
    AnalyzeRequest,
    AnalyzeResponse,
    BatchItem,
    BatchRequest,
    BatchResponse,
    Comparison,
    ResultResponse,
)
from app.services import images, inference, worker

router = APIRouter(prefix="/xray")


def queue(request: AnalyzeRequest, db: sqlite3.Connection) -> str:
    scan_id = uuid4().hex
    try:
        image_path = images.store(request.image, scan_id)
    except images.InvalidImage as exc:
        raise ApiError(422, exc.error, str(exc) or None)
    with db:
        db.execute(
            "INSERT INTO scans (id, facility_id, patient_ref, image_path) VALUES (?, ?, ?, ?)",
            (scan_id, request.facility_id, request.patient_ref, str(image_path)),
        )
    return scan_id


@router.post("/analyze", response_model=AnalyzeResponse, status_code=202)
def analyze(request: AnalyzeRequest, db: sqlite3.Connection = Depends(get_db)) -> AnalyzeResponse:
    if not inference.model_ready():
        raise ApiError(503, "model_unavailable")
    scan_id = queue(request, db)
    worker.wake()
    return AnalyzeResponse(scan_id=scan_id, status="pending")


@router.post("/analyze/batch", response_model=BatchResponse, status_code=202)
def analyze_batch(request: BatchRequest, db: sqlite3.Connection = Depends(get_db)) -> BatchResponse:
    if not inference.model_ready():
        raise ApiError(503, "model_unavailable")
    items = []
    for index, scan in enumerate(request.scans):
        try:
            items.append(BatchItem(index=index, scan_id=queue(scan, db), status="pending"))
        except ApiError as exc:
            items.append(BatchItem(index=index, error=exc.error, message=exc.message))
    worker.wake()
    return BatchResponse(scans=items)


@router.get("/results/{scan_id}", response_model=ResultResponse)
def result(scan_id: str, db: sqlite3.Connection = Depends(get_db)) -> ResultResponse:
    row = db.execute(
        "SELECT s.facility_id, s.patient_ref, s.status, s.error,"
        " r.findings, r.flagged_regions, r.body_part, r.conditions, r.devices, r.boxes, r.comparison,"
        " r.confidence,"
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
    scan = {"scan_id": scan_id, "facility_id": row["facility_id"], "patient_ref": row["patient_ref"]}
    if row["status"] != "complete":
        return ResultResponse(
            **scan,
            status=row["status"],
            error=row["error"],
            message=MESSAGES.get(row["error"]),
        )

    return ResultResponse(
        **scan,
        status="complete",
        body_part=row["body_part"],
        conditions=json.loads(row["conditions"]),
        findings=json.loads(row["findings"]),
        flagged_regions=json.loads(row["flagged_regions"]),
        devices=json.loads(row["devices"]),
        boxes=json.loads(row["boxes"]),
        comparison=Comparison.model_validate_json(row["comparison"]) if row["comparison"] else None,
        confidence=row["confidence"],
        requires_review=bool(row["requires_review"]),
        synced_to_dhis2=row["sync_status"] == "synced",
    )
