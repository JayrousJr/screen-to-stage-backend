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
| DICOM (`.dcm`) | Not yet |

Use frontal (PA or AP) chest X-rays. Public test sets: the Shenzhen and Montgomery chest X-ray sets from the US National Library of Medicine.

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
  "error": null
}
```

`requires_review` is `true` whenever the reading is abnormal or the confidence is not high.

### 4. Failure cases

| Test | How | Expect |
| --- | --- | --- |
| Unreadable image | Submit `"image": "hello"` | 422 `invalid_image` |
| Missing field | Submit without `facility_id` | 422 `invalid_request` |
| Model down | Stop Ollama, then submit | 503 `model_unavailable` |
| Model drops mid-queue | Submit, then stop Ollama before it completes | Scan stays `pending`, completes when Ollama is back |
| Power cut | Submit, kill the backend while `processing`, start it again | Scan completes on its own |
| Slow model | Set `INFERENCE_TIMEOUT_SECONDS=1` in `.env`, restart, submit | 504 `inference_timeout` on the result |
| Unknown scan | `GET /api/xray/results/abc` | 404 `scan_not_found` |

Every error has the shape `{"error": "...", "detail": "..."}`; `detail` is present only when there is more to say.

## Automated tests

```
python -m pytest
```

These run without Ollama; the model is replaced by a stub.

## Data

Scans and results are stored in `data/screen_to_stage.db`, images in `data/images/`. Delete the `data/` folder to start clean.
