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


CONDITIONS = {
    "consolidation": "Consolidation or pneumonia",
    "tb_signs": "Possible TB signs",
    "pleural_effusion": "Fluid around the lung (pleural effusion)",
    "pneumothorax": "Air around the lung (pneumothorax)",
    "cardiomegaly": "Enlarged heart (cardiomegaly)",
    "vascular_congestion": "Congested lung vessels",
    "nodule_or_mass": "Nodule or mass",
    "bone_abnormality": "Rib or bone abnormality",
    "spine_curvature": "Curved spine",
    "device_misplaced": "Tube, line or device out of position",
    "other_abnormality": "Other abnormality",
}


class ChestChecklist(BaseModel):
    consolidation: bool
    tb_signs: bool
    pleural_effusion: bool
    pneumothorax: bool
    cardiomegaly: bool
    vascular_congestion: bool
    nodule_or_mass: bool
    bone_abnormality: bool
    spine_curvature: bool
    device_misplaced: bool
    other_abnormality: bool


class ModelFindings(BaseModel):
    checklist: ChestChecklist
    findings: list[str]
    flagged_regions: list[str]
    devices: list[str]
    confidence: Confidence

    @property
    def conditions(self) -> list[str]:
        return [label for name, label in CONDITIONS.items() if getattr(self.checklist, name)]

    @property
    def requires_review(self) -> bool:
        return bool(self.conditions) or self.confidence != "high"


class ResultResponse(BaseModel):
    scan_id: str
    facility_id: str
    patient_ref: str
    status: ScanStatus
    conditions: list[str] = []
    findings: list[str] = []
    flagged_regions: list[str] = []
    devices: list[str] = []
    confidence: Confidence | None = None
    requires_review: bool = True
    synced_to_dhis2: bool = False
    error: str | None = None
    message: str | None = None
