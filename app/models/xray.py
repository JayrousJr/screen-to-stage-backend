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


class ImageCheck(BaseModel):
    is_chest_xray: bool
    is_frontal: bool


class ModelFindings(BaseModel):
    findings: list[str]
    flagged_regions: list[str]
    abnormal: bool
    confidence: Confidence

    @property
    def requires_review(self) -> bool:
        return self.abnormal or self.confidence != "high"


class ResultResponse(BaseModel):
    scan_id: str
    status: ScanStatus
    findings: list[str] = []
    flagged_regions: list[str] = []
    confidence: Confidence | None = None
    requires_review: bool = True
    synced_to_dhis2: bool = False
    error: str | None = None
    message: str | None = None
