from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["low", "medium", "high"]
ScanStatus = Literal["pending", "processing", "complete", "failed"]


class AnalyzeRequest(BaseModel):
    image: str = Field(min_length=1)
    facility_id: str = Field(min_length=1)
    patient_ref: str = Field(min_length=1)


class AnalyzeResponse(BaseModel):
    scan_id: str
    status: ScanStatus


BodyPartName = Literal["chest", "bone_joint", "abdomen", "dental_head"]
Change = Literal["better", "same", "worse", "new_finding", "unclear"]


class ImageCheck(BaseModel):
    is_xray: bool
    body_part: Literal["chest", "bone_joint", "abdomen", "dental_head", "other"]
    is_frontal: bool


class ModelBox(BaseModel):
    label: str
    box: list[float]


class Box(BaseModel):
    label: str
    box: list[int]


class Reading(BaseModel):
    body_part: BodyPartName
    conditions: list[str]
    findings: list[str]
    flagged_regions: list[str]
    devices: list[str]
    boxes: list[Box] = []
    confidence: Confidence

    @property
    def requires_review(self) -> bool:
        return bool(self.conditions) or self.confidence != "high"


class ModelComparison(BaseModel):
    change: Change
    summary: str


class Comparison(ModelComparison):
    previous_scan_id: str


class ResultResponse(BaseModel):
    scan_id: str
    facility_id: str
    patient_ref: str
    status: ScanStatus
    body_part: BodyPartName | None = None
    conditions: list[str] = []
    findings: list[str] = []
    flagged_regions: list[str] = []
    devices: list[str] = []
    boxes: list[Box] = []
    comparison: Comparison | None = None
    confidence: Confidence | None = None
    requires_review: bool = True
    synced_to_dhis2: bool = False
    error: str | None = None
    message: str | None = None


class BatchRequest(BaseModel):
    scans: list[AnalyzeRequest] = Field(min_length=1, max_length=20)


class BatchItem(BaseModel):
    index: int
    scan_id: str | None = None
    status: ScanStatus | None = None
    error: str | None = None
    message: str | None = None


class BatchResponse(BaseModel):
    scans: list[BatchItem]
