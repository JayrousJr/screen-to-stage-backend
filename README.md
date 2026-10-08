# Screen-to-Stage Backend

Offline chest X-ray screening API. A health worker's mobile app submits an X-ray, MedGemma reads it on the device through Ollama, and the result is flagged for clinician review. It never returns a diagnosis.

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

Use front (PA or AP) chest X-rays. Side (lateral) views are not read. Phone photos are turned upright using their orientation tag.

DICOM files are shown with the window stored in the file, and inverted files (`MONOCHROME1`) are flipped so bones are white. A DICOM marked as a side view (`ViewPosition` of `LL` or `RL`) is rejected at upload.

Other images are rejected in two steps: colour images at upload (`MAX_COLOUR_SPREAD` in `.env`; raise it if phone photos of films get rejected), then the model is asked whether the image is a front chest X-ray before it reads it. Public test sets: the Shenzhen and Montgomery chest X-ray sets from the US National Library of Medicine.

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
  "status": "complete",
  "findings": ["There are increased interstitial markings in the right upper lobe."],
  "flagged_regions": ["right upper zone"],
  "confidence": "high",
  "requires_review": true,
  "synced_to_dhis2": false,
  "error": null,
  "message": null
}
```

`requires_review` is `true` whenever the reading is abnormal or the confidence is not high.

### 4. Failure cases

| Test | How | Expect |
| --- | --- | --- |
| Unreadable image | Submit `"image": "hello"` | 422 `invalid_image` |
| Colour photo | Submit a colour photo, such as a tree | 422 `invalid_image` |
| Not a chest X-ray | Submit a greyscale image that is not a chest X-ray | Result `failed` with error `not_chest_xray` |
| Side view | Submit a lateral chest X-ray | Result `failed` with error `not_frontal_view` |
| Side view DICOM | Submit a DICOM with `ViewPosition` `LL` | 422 `not_frontal_view` |
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
  --not-chest other_images \
  --side-view side_views
```

`--not-chest` and `--side-view` are optional: folders of images that should be rejected, such as photos, hand or knee X-rays, and lateral chest X-rays.

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
| Non-chest images rejected | Photos and other X-rays turned away | Close to 100% |
| Side views rejected | Lateral views turned away | Close to 100% |

The 90% and 70% aims are the WHO targets for TB triage tests. Each figure comes with a 95% range: the true figure is likely to lie inside it. With few images the range is wide, so run the full sets before trusting a number.

## Data

Scans and results are stored in `data/screen_to_stage.db`, images in `data/images/`. Delete the `data/` folder to start clean.
