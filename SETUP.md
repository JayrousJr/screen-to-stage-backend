# Setup

Works on a laptop or a Raspberry Pi 5. Internet is needed only for steps 1–3.

## 1. Install Ollama and pull the model

```
curl -fsSL https://ollama.com/install.sh | sh
ollama pull medgemma1.5:4b
```

Pick the model version that fits the device:

| Version | Download | Memory | Use on |
| --- | --- | --- | --- |
| `medgemma1.5:4b` | ~3.3 GB | ~6 GB | Raspberry Pi 5, laptops with 8 GB |
| `medgemma1.5:4b-it-q8_0` | ~5 GB | ~8 GB | Laptops with 16 GB |
| `medgemma1.5:4b-it-bf16` | ~8.6 GB | ~12 GB | Laptops with 16 GB or more, or a GPU with 12 GB or more |

Bigger versions are more accurate and slower. bf16 is the full model as Google tested it; on a CPU it can take two to three times as long per X-ray, so raise `INFERENCE_TIMEOUT_SECONDS` if readings time out. To use one, pull it and set `OLLAMA_MODEL` in `.env` to the same tag.

On Linux the installer starts Ollama as a service. If it is not running, start it with `ollama serve`.

## 2. Get the code

```
git clone <repo-url>
cd screen-to-stage-backend
```

## 3. Install Python dependencies

```
sudo apt install python3-venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 4. Configure

```
cp .env.example .env
```

| Variable        | Purpose                                          |
| --------------- | ------------------------------------------------ |
| `OLLAMA_HOST`   | Ollama address, default `http://localhost:11434` |
| `OLLAMA_MODEL`  | Model tag, default `medgemma1.5:4b`              |
| `DHIS2_URL`     | DHIS2 server; leave empty to keep results queued |
| `DHIS2_TOKEN`   | DHIS2 access token                               |
| `DATABASE_PATH` | SQLite file, created on first start              |
| `IMAGE_DIR`     | Where uploaded images are stored                 |

## 5. Run

From the project root:

```
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` lets the mobile app reach the backend over the local network at `http://<device-ip>:8000`.

## 6. Check

```
curl http://localhost:8000/api/health
```

`"status": "ok"` means the backend, Ollama and the model are all ready. `"degraded"` means Ollama is not running or the model is not pulled.

## Tests

```
python -m pytest
```
