# Screen-to-Stage Backend

Offline X-ray screening API. A health worker's mobile app submits an X-ray, MedGemma reads it on the device through Ollama, and the result is flagged for clinician review. It never returns a diagnosis.

Installation is in [SETUP.md](SETUP.md).

## Run

From the project root, with Ollama running:

```
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Endpoints

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/health` | GET | Is the backend up, and is the model ready |
| `/api/xray/analyze` | POST | Submit an X-ray, get a `scan_id` back immediately |
| `/api/xray/analyze/batch` | POST | Submit up to 20 X-rays at once, get a `scan_id` for each |
| `/api/xray/results/{scan_id}` | GET | Poll for the result |

Interactive API page: http://localhost:8000/docs

## Image formats

The image is sent base64-encoded in the `image` field. A `data:image/...;base64,` prefix is accepted.

| Format | Supported |
| --- | --- |
| PNG | Yes, 8-bit and 16-bit |
| JPEG | Yes |
| TIFF | Yes, 8-bit and 16-bit |
| BMP | Yes |
| WebP | Yes |
| GIF | Yes, first frame |
| DICOM (`.dcm`) | Yes: uncompressed, JPEG, JPEG 2000 and RLE; first frame |

Phone photos are turned upright using their orientation tag. DICOM files are shown with the window stored in the file, and inverted files (`MONOCHROME1`) are flipped so bones are white.

Images that are not X-rays are rejected in two steps: colour images at upload (`MAX_COLOUR_SPREAD` in `.env`; raise it if phone photos of films get rejected), then the model is asked whether the image is an X-ray and of which body part before it reads it.

## Body parts

Each body part is read against its own checklist:

| Body part | `body_part` | Checklist |
| --- | --- | --- |
| Chest | `chest` | Pneumonia, signs of TB, fluid or air around the lung, enlarged heart, congested lung vessels, nodule or mass, rib or bone problem, curved spine, tube or device out of place |
| Bones and joints | `bone_joint` | Fracture, dislocation, arthritis signs, bone lesion, possible bone infection, thin bones, curvature or alignment, implant problem, foreign body |
| Abdomen | `abdomen` | Dilated bowel, air-fluid levels, possible volvulus, free air, stones or calcification, foreign body |
| Teeth, face and skull | `dental_head` | Tooth decay, root abscess, gum bone loss, impacted tooth, fracture, skull shape problem |

Every checklist also has "other abnormality". Chest X-rays must be front views (PA or AP); a side view fails with `not_frontal_view`, and so does a DICOM marked as a chest side view. Side views of bones are read, since fractures are often checked on two views.

The chest checklist is what MedGemma is best at. Bones, abdomen and teeth have not been checked against a test set yet, so treat those readings with more care. To read only some body parts, set `BODY_PARTS` in `.env`, for example `BODY_PARTS=["chest"]`; other X-rays then fail with `unsupported_body_part`.

CT, MRI, fluoroscopy, angiography and mammography are not supported: they need many images or video, or specialist reading. Bone density (DEXA) is a measurement, not an image to read. "Bones look thin" on a plain X-ray is only a hint for a clinician. Public test sets: the Shenzhen and Montgomery chest X-ray sets from the US National Library of Medicine.

## Testing by hand

### 1. Check health

```
curl -s localhost:8000/api/health
```

Expect `"status": "ok"`.

### 2. Submit an X-ray

Replace `xray.jpg` with your image:

```
python3 -c 'import base64, json, sys; print(json.dumps({"image": base64.b64encode(open(sys.argv[1], "rb").read()).decode(), "facility_id": "FAC-001", "patient_ref": "P-001"}))' xray.jpg > payload.json

curl -s -X POST localhost:8000/api/xray/analyze -H "Content-Type: application/json" -d @payload.json
```

Response (HTTP 202):

```json
{"scan_id": "a65f5d6e17984e009776609eac249b4f", "status": "pending"}
```

### 3. Get the result

```
curl -s localhost:8000/api/xray/results/<scan_id>
```

The status moves `pending` → `processing` → `complete`. Repeat the call until it is `complete`:

```json
{
  "scan_id": "a65f5d6e17984e009776609eac249b4f",
  "facility_id": "FAC-001",
  "patient_ref": "P-001",
  "status": "complete",
  "body_part": "chest",
  "conditions": ["Possible TB signs"],
  "findings": ["There is a patchy opacity in the right upper zone.", "The heart size is normal."],
  "flagged_regions": ["right upper zone"],
  "devices": [],
  "boxes": [{"label": "patchy opacity", "box": [180, 140, 420, 330]}],
  "comparison": {
    "previous_scan_id": "0d1f6c2b9e8a4d7f8b3c5a6e7f8091a2",
    "change": "worse",
    "summary": "The right upper zone opacity is larger than before."
  },
  "confidence": "high",
  "requires_review": true,
  "synced_to_dhis2": false,
  "error": null,
  "message": null
}
```

- `conditions`: what the checklist found, in plain words.
- `devices`: tubes, lines, implants and other man-made objects seen.
- `boxes`: where each abnormal finding is, as `[x_min, y_min, x_max, y_max]` from 0 to 1000 across the image. Multiply by the image width or height and divide by 1000 to get pixels. Box positions come from the model and have not been checked; use them to point a clinician to an area, not as a measurement. Set `LOCATE_FINDINGS=false` to leave them out.
- `comparison`: when the same `facility_id` and `patient_ref` already has a completed scan of the same body part, the new X-ray is compared with the latest one. `change` is `better`, `same`, `worse`, `new_finding` or `unclear`. It is `null` for a first scan, or if the comparison could not be made; the reading itself is still returned. Set `COMPARE_WITH_PREVIOUS=false` to turn it off.

`requires_review` is `true` whenever any condition is found, the confidence is not high, or the comparison says `worse` or `new_finding`.

### Submitting several X-rays at once

```
curl -s -X POST localhost:8000/api/xray/analyze/batch -H "Content-Type: application/json" \
  -d '{"scans": [{"image": "...", "facility_id": "FAC-001", "patient_ref": "P-001"}, {"image": "...", "facility_id": "FAC-001", "patient_ref": "P-002"}]}'
```

Response (HTTP 202), one item per scan in the same order:

```json
{"scans": [
  {"index": 0, "scan_id": "a65f5d6e17984e009776609eac249b4f", "status": "pending", "error": null, "message": null},
  {"index": 1, "scan_id": null, "status": null, "error": "invalid_image", "message": "This is a colour photo. Please upload the X-ray image itself."}
]}
```

A bad image fails on its own; the rest are still queued. Poll each `scan_id` as usual. The X-rays are read one at a time, in order.

### 4. Failure cases

| Test | How | Expect |
| --- | --- | --- |
| Unreadable image | Submit `"image": "hello"` | 422 `invalid_image` |
| Colour photo | Submit a colour photo, such as a tree | 422 `invalid_image` |
| Not an X-ray | Submit a greyscale photo or document | Result `failed` with error `not_xray` |
| Body part switched off | Set `BODY_PARTS=["chest"]`, submit a hand X-ray | Result `failed` with error `unsupported_body_part` |
| Chest side view | Submit a lateral chest X-ray | Result `failed` with error `not_frontal_view` |
| Chest side view DICOM | Submit a DICOM with `ViewPosition` `LL` and `BodyPartExamined` `CHEST` | 422 `not_frontal_view` |
| Batch too big | Submit 21 scans to `/api/xray/analyze/batch` | 422 `invalid_request` |
| Missing field | Submit without `facility_id` | 422 `invalid_request` |
| Model down | Stop Ollama, then submit | 503 `model_unavailable` |
| Model drops mid-queue | Submit, then stop Ollama before it completes | Scan stays `pending`, completes when Ollama is back |
| Power cut | Submit, kill the backend while `processing`, start it again | Scan completes on its own |
| Slow model | Set `INFERENCE_TIMEOUT_SECONDS=1` in `.env`, restart, submit | 504 `inference_timeout` on the result |
| Unknown scan | `GET /api/xray/results/abc` | 404 `scan_not_found` |

Every error has the shape `{"error": "...", "message": "...", "detail": "..."}`. `error` is a fixed code for the app to check, `message` is a sentence the app can show the health worker as is, and `detail` lists the wrong fields of an `invalid_request`. A `failed` result carries the same `error` and `message`.

## Automated tests

```
python -m pytest
```

These run without Ollama; the model is replaced by a stub.

## Measuring accuracy

`scripts/evaluate.py` sends a folder of X-rays through the running backend and reports how well it screens them. Start the backend with Ollama first.

Get the Shenzhen and Montgomery sets from the US National Library of Medicine. Their file names end in `_0` (normal) or `_1` (TB), which is how the script knows the answer. Pass the folder that holds the X-rays (`CXR_png`), not the one holding the lung masks.

```
python scripts/evaluate.py \
  --labelled ChinaSet_AllFiles/CXR_png \
  --labelled MontgomerySet/CXR_png \
  --reject photos \
  --side-view side_views
```

`--reject` and `--side-view` are optional: folders of images that should be rejected, such as photos and documents, and lateral chest X-rays. Bone, abdomen and dental X-rays are read now, so do not put them in `--reject`.

For sets that sort images into folders instead, such as TBX11K, say what each folder holds:

```
python scripts/evaluate.py \
  --normal TBX11K/imgs/health \
  --tb TBX11K/imgs/tb \
  --sick TBX11K/imgs/sick \
  --sample 200
```

`--sick` is for lung disease other than TB; those X-rays should still be sent for review. TBX11K's `imgs/test` folder has no answers released, so leave it out. `--sample 200` picks 200 images of each kind at random, the same ones every run, which keeps a large set to a few hours.

Each result is written to `evaluation.csv` as soon as it arrives. If the run stops, run the same command again and it carries on where it left off. Images run in a mixed order, so a part-finished run still gives a fair picture. Use a different `--out` file for each set so their results stay apart. Use `--limit 20` for a quick first try, and `--summary-only` to print the report again.

The report gives:

| Line | Meaning | Aim for |
| --- | --- | --- |
| Sensitivity | TB cases sent for review | 90% or more |
| Specificity | Normal cases not sent for review | 70% or more |
| Review load | Normal cases sent for review anyway | As low as sensitivity allows |
| Other lung disease flagged | Non-TB disease sent for review | High |
| Real chest X-rays wrongly rejected | Good X-rays the checks turned away | Close to 0% |
| Images that should be rejected, rejected | Photos and documents turned away | Close to 100% |
| Side views rejected | Lateral views turned away | Close to 100% |

The 90% and 70% aims are the WHO targets for TB triage tests. Each figure comes with a 95% range: the true figure is likely to lie inside it. With few images the range is wide, so run the full sets before trusting a number.

## Data

Scans and results are stored in `data/screen_to_stage.db`, images in `data/images/`. Delete the `data/` folder to start clean.
