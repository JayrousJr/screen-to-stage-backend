from app.models.xray import CONDITIONS, ChestChecklist, ModelFindings


def reading(*present: str, findings=("Clear lung fields",), regions=(), devices=(), confidence="high") -> ModelFindings:
    return ModelFindings(
        checklist=ChestChecklist(**{name: name in present for name in CONDITIONS}),
        findings=list(findings),
        flagged_regions=list(regions),
        devices=list(devices),
        confidence=confidence,
    )
