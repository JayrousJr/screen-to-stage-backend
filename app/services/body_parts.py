from dataclasses import dataclass, field

from pydantic import BaseModel, Field, create_model

from app.models.xray import Confidence, ModelBox


@dataclass(frozen=True)
class BodyPart:
    name: str
    label: str
    subject: str
    region_example: str
    conditions: dict[str, tuple[str, str]]
    output: type[BaseModel] = field(init=False)

    def __post_init__(self):
        checklist = create_model(
            f"{self.name.title().replace('_', '')}Checklist",
            **{condition: (bool, ...) for condition in self.conditions},
        )
        output = create_model(
            f"{self.name.title().replace('_', '')}Reading",
            checklist=(checklist, ...),
            findings=(list[str], ...),
            flagged_regions=(list[str], ...),
            devices=(list[str], ...),
            boxes=(list[ModelBox], Field(default_factory=list)),
            confidence=(Confidence, ...),
        )
        object.__setattr__(self, "output", output)

    def prompt(self, locate: bool) -> str:
        items = "; ".join(f"{name}, {description}" for name, (_, description) in self.conditions.items())
        parts = [
            f"You are assisting a clinician by screening {self.subject}. Look at the whole image carefully.",
            "Report in JSON.",
            f"checklist: answer true or false for each item, true if it is visible even if subtle: {items}.",
            "findings, one short sentence per observation, normal and abnormal;",
            f"flagged_regions, the names of regions that look abnormal, such as '{self.region_example}', or an empty list;",
            "devices, the tubes, lines, implants, plates, screws or other man-made objects visible, or an empty list;",
        ]
        if locate:
            parts.append(
                "boxes, for each abnormal finding a label and a box [x_min, y_min, x_max, y_max] "
                "in coordinates from 0 to 1000 across the image, or an empty list;"
            )
        parts += [
            "confidence, how sure you are of this reading.",
            "When unsure whether something is abnormal, answer true so a clinician checks it.",
            "Describe what is visible. Do not give a diagnosis.",
        ]
        return " ".join(parts)

    def labels(self, checklist: BaseModel) -> list[str]:
        return [label for name, (label, _) in self.conditions.items() if getattr(checklist, name)]


CHEST = BodyPart(
    name="chest",
    label="Chest",
    subject="a chest X-ray",
    region_example="right upper zone",
    conditions={
        "consolidation": ("Consolidation or pneumonia", "patchy or dense opacity suggesting pneumonia"),
        "tb_signs": (
            "Possible TB signs",
            "upper zone opacity, cavities, enlarged hilar lymph nodes, miliary nodules or fibrotic scarring",
        ),
        "pleural_effusion": (
            "Fluid around the lung (pleural effusion)",
            "fluid blunting the costophrenic angle or layering at the base",
        ),
        "pneumothorax": (
            "Air around the lung (pneumothorax)",
            "a visible pleural line with no lung markings beyond it",
        ),
        "cardiomegaly": ("Enlarged heart (cardiomegaly)", "heart wider than half the chest on a PA view"),
        "vascular_congestion": (
            "Congested lung vessels",
            "prominent upper lobe vessels, Kerley lines or interstitial oedema",
        ),
        "nodule_or_mass": ("Nodule or mass", "any round opacity in the lung or mediastinum"),
        "bone_abnormality": ("Rib or bone abnormality", "rib or clavicle fracture, lytic or sclerotic bone lesion"),
        "spine_curvature": ("Curved spine", "visible scoliosis"),
        "device_misplaced": (
            "Tube, line or device out of position",
            "an endotracheal tube, central line, nasogastric tube, pacemaker or other device in the wrong position",
        ),
        "other_abnormality": ("Other abnormality", "anything else abnormal"),
    },
)

BONE_JOINT = BodyPart(
    name="bone_joint",
    label="Bones and joints",
    subject="an X-ray of bones or joints",
    region_example="distal radius",
    conditions={
        "fracture": ("Fracture", "a break, crack, buckle or stress fracture line in any bone"),
        "dislocation": ("Dislocated joint", "joint surfaces out of line or not meeting"),
        "arthritis": (
            "Arthritis signs",
            "joint space narrowing, bone spurs, erosions or bone sclerosis next to a joint",
        ),
        "bone_lesion": (
            "Bone lesion",
            "a lytic (dark) or blastic (bright) area, a bone tumour or metastasis",
        ),
        "bone_infection": (
            "Possible bone infection",
            "bone destruction, periosteal reaction or soft tissue swelling suggesting osteomyelitis",
        ),
        "thin_bones": (
            "Bones look thin (possible low density)",
            "thin cortex or bones that look less dense than normal",
        ),
        "alignment": (
            "Curvature or alignment problem",
            "scoliosis, kyphosis, or bones out of their normal alignment",
        ),
        "hardware_problem": (
            "Implant problem",
            "a plate, screw, rod or joint replacement that is broken, loose or out of position",
        ),
        "foreign_body": ("Foreign body", "a radiopaque object that does not belong"),
        "other_abnormality": ("Other abnormality", "anything else abnormal"),
    },
)

ABDOMEN = BodyPart(
    name="abdomen",
    label="Abdomen",
    subject="an abdominal X-ray",
    region_example="left upper quadrant",
    conditions={
        "dilated_bowel": (
            "Dilated bowel (possible obstruction)",
            "small bowel wider than 3 cm or colon wider than 6 cm",
        ),
        "air_fluid_levels": ("Air-fluid levels", "flat lines between air and fluid inside bowel loops"),
        "volvulus_sign": ("Possible volvulus", "a coffee bean shaped, hugely dilated loop of bowel"),
        "free_air": (
            "Free air (possible perforation)",
            "air under the diaphragm or air outlining both sides of the bowel wall (Rigler's sign)",
        ),
        "stone_or_calcification": (
            "Stone or calcification",
            "kidney, ureter, bladder or gallbladder stones, or other calcification",
        ),
        "foreign_body": ("Foreign body", "a swallowed or inserted radiopaque object"),
        "other_abnormality": ("Other abnormality", "anything else abnormal"),
    },
)

DENTAL_HEAD = BodyPart(
    name="dental_head",
    label="Teeth, face and skull",
    subject="a dental, facial or skull X-ray",
    region_example="lower left first molar",
    conditions={
        "tooth_decay": ("Tooth decay", "dark areas in the enamel or dentine of a tooth"),
        "root_abscess": ("Possible root abscess", "a dark area around the tip of a tooth root"),
        "periodontal_bone_loss": ("Gum bone loss", "the bone around the teeth lower than normal"),
        "impacted_tooth": ("Impacted tooth", "a tooth, often a wisdom tooth, that is stuck and cannot come through"),
        "fracture": ("Fracture", "a break in the jaw, face or skull"),
        "skull_shape": (
            "Skull shape problem",
            "an abnormal skull shape or closed sutures in a child (possible craniosynostosis)",
        ),
        "other_abnormality": ("Other abnormality", "anything else abnormal"),
    },
)

BODY_PARTS = {part.name: part for part in (CHEST, BONE_JOINT, ABDOMEN, DENTAL_HEAD)}
