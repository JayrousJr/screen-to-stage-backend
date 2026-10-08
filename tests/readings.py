from app.models.xray import Reading
from app.services.body_parts import BODY_PARTS


def reading(
    *present: str,
    body_part="chest",
    findings=("Clear lung fields",),
    regions=(),
    devices=(),
    boxes=(),
    confidence="high",
) -> Reading:
    conditions = BODY_PARTS[body_part].conditions
    return Reading(
        body_part=body_part,
        conditions=[label for name, (label, _) in conditions.items() if name in present],
        findings=list(findings),
        flagged_regions=list(regions),
        devices=list(devices),
        boxes=list(boxes),
        confidence=confidence,
    )
