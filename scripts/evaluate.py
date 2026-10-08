import argparse
import base64
import csv
import math
import random
import re
import sys
import time
from pathlib import Path

import httpx

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".gif", ".dcm"}
COLUMNS = [
    "set", "file", "expected", "outcome", "requires_review", "confidence", "conditions", "flagged_regions", "seconds",
]
REJECTIONS = {"invalid_image", "not_chest_xray", "not_frontal_view"}


def label(path: Path) -> str | None:
    match = re.search(r"_([01])$", path.stem)
    if match is None:
        return None
    return "tb" if match.group(1) == "1" else "normal"


def images_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_TYPES)


def cases(args: argparse.Namespace) -> list[tuple[str, Path, str]]:
    shuffle = random.Random(0)

    def pick(paths: list[Path]) -> list[Path]:
        if args.sample and len(paths) > args.sample:
            return sorted(shuffle.sample(paths, args.sample))
        return paths

    found = []
    for folder in args.labelled:
        for path in images_in(folder):
            expected = label(path)
            if expected is None:
                print(f"skipped, no _0 or _1 at the end of the name: {path}", file=sys.stderr)
                continue
            found.append((folder.name, path, expected))
    for expected, folders in (
        ("normal", args.normal),
        ("tb", args.tb),
        ("sick", args.sick),
        ("not_chest", args.not_chest),
        ("side_view", args.side_view),
    ):
        found += [(folder.name, path, expected) for folder in folders for path in images_in(folder)]
    by_expected = {}
    for case in found:
        by_expected.setdefault(case[2], []).append(case)
    found = [case for group in by_expected.values() for case in pick(group)]
    shuffle.shuffle(found)
    return found


def submit(client: httpx.Client, path: Path) -> httpx.Response:
    payload = {
        "image": base64.b64encode(path.read_bytes()).decode(),
        "facility_id": "EVAL",
        "patient_ref": path.stem,
    }
    while True:
        response = client.post("/api/xray/analyze", json=payload)
        if response.status_code != 503:
            return response
        print("model not ready, waiting 30 s", file=sys.stderr)
        time.sleep(30)


def run_one(client: httpx.Client, path: Path, poll_seconds: float) -> dict:
    started = time.monotonic()
    response = submit(client, path)
    if response.status_code != 202:
        return {"outcome": response.json().get("error", f"http_{response.status_code}")}
    scan_id = response.json()["scan_id"]
    while True:
        time.sleep(poll_seconds)
        result = client.get(f"/api/xray/results/{scan_id}")
        if result.status_code != 200:
            return {"outcome": result.json().get("error", f"http_{result.status_code}")}
        body = result.json()
        if body["status"] == "failed":
            return {"outcome": body["error"]}
        if body["status"] == "complete":
            return {
                "outcome": "read",
                "requires_review": body["requires_review"],
                "confidence": body["confidence"],
                "conditions": "; ".join(body.get("conditions", [])),
                "flagged_regions": "; ".join(body["flagged_regions"]),
                "seconds": round(time.monotonic() - started, 1),
            }


def wilson(hits: int, total: int) -> tuple[float, float]:
    if total == 0:
        return (math.nan, math.nan)
    z = 1.96
    p = hits / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return (centre - margin, centre + margin)


def rate(hits: int, total: int) -> str:
    if total == 0:
        return "n/a (no images)"
    low, high = wilson(hits, total)
    return f"{hits / total:6.1%}  ({hits}/{total}, 95% range {low:.0%} to {high:.0%})"


def summarise(rows: list[dict]) -> str:
    def where(**conditions):
        return [r for r in rows if all(r[k] == v for k, v in conditions.items())]

    flagged = lambda r: r["outcome"] == "read" and str(r["requires_review"]) == "True"
    tb, normal = where(expected="tb"), where(expected="normal")
    tb_read, normal_read = where(expected="tb", outcome="read"), where(expected="normal", outcome="read")
    chest = tb + normal + where(expected="sick")
    chest_rejected = [r for r in chest if r["outcome"] in REJECTIONS]
    not_chest, side = where(expected="not_chest"), where(expected="side_view")
    seconds = [float(r["seconds"]) for r in rows if r["seconds"]]

    line = lambda name, value: f"  {name + ':':<47}{value}"
    lines = [
        "Screening (chest X-rays with a TB label)",
        line("TB cases flagged for review (sensitivity)", rate(sum(map(flagged, tb_read)), len(tb_read))),
        line("Normal cases not flagged (specificity)", rate(sum(not flagged(r) for r in normal_read), len(normal_read))),
        line("Normal cases flagged anyway (review load)", rate(sum(map(flagged, normal_read)), len(normal_read))),
        line("TB cases flagged, counting rejects as missed", rate(sum(map(flagged, tb)), len(tb))),
        line("Other lung disease flagged for review", rate(sum(map(flagged, where(expected="sick", outcome="read"))), len(where(expected="sick", outcome="read")))),
        "",
        "Image checks",
        line("Real chest X-rays wrongly rejected", rate(len(chest_rejected), len(chest))),
        line("Non-chest images rejected", rate(sum(r["outcome"] in REJECTIONS for r in not_chest), len(not_chest))),
        line("Side views rejected as side views", rate(sum(r["outcome"] == "not_frontal_view" for r in side), len(side))),
        line("Side views rejected for any reason", rate(sum(r["outcome"] in REJECTIONS for r in side), len(side))),
    ]
    if chest_rejected:
        reasons = {}
        for r in chest_rejected:
            reasons[r["outcome"]] = reasons.get(r["outcome"], 0) + 1
        lines.append(line("Why real chest X-rays were rejected", ", ".join(f"{k} {v}" for k, v in sorted(reasons.items()))))
    failures = [r for r in rows if r["outcome"] not in REJECTIONS | {"read"}]
    if failures:
        lines.append(line("Other failures (timeouts, unreadable output)", len(failures)))
    lines.append("")
    lines.append("Confidence the model gave")
    for name, read in (("TB", tb_read), ("normal", normal_read)):
        counts = ", ".join(f"{level} {sum(r['confidence'] == level for r in read)}" for level in ("high", "medium", "low"))
        lines.append(f"  {name + ' cases:':<14}{counts}")
    if seconds:
        seconds.sort()
        lines.append("")
        lines.append(f"Time per reading: median {seconds[len(seconds) // 2]:.0f} s, slowest {seconds[-1]:.0f} s")
    return "\n".join(lines)


def load(out: Path) -> list[dict]:
    if not out.exists():
        return []
    with open(out, newline="") as file:
        return list(csv.DictReader(file))


def header(out: Path) -> list[str]:
    with open(out, newline="") as file:
        return csv.DictReader(file).fieldnames or COLUMNS


def main() -> None:
    parser = argparse.ArgumentParser(description="Measure how well the backend screens chest X-rays.")
    parser.add_argument("--labelled", type=Path, action="append", default=[],
                        help="folder of chest X-rays named ..._0 (normal) or ..._1 (TB), as in Shenzhen and Montgomery")
    parser.add_argument("--normal", type=Path, action="append", default=[],
                        help="folder of healthy chest X-rays, such as TBX11K imgs/health")
    parser.add_argument("--tb", type=Path, action="append", default=[],
                        help="folder of chest X-rays with TB, such as TBX11K imgs/tb")
    parser.add_argument("--sick", type=Path, action="append", default=[],
                        help="folder of chest X-rays with lung disease other than TB, such as TBX11K imgs/sick")
    parser.add_argument("--not-chest", type=Path, action="append", default=[],
                        help="folder of images that are not chest X-rays")
    parser.add_argument("--side-view", type=Path, action="append", default=[],
                        help="folder of side-view (lateral) chest X-rays")
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--out", type=Path, default=Path("evaluation.csv"))
    parser.add_argument("--sample", type=int, help="use at most this many images of each kind, picked at random")
    parser.add_argument("--limit", type=int, help="only run this many new images")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--summary-only", action="store_true", help="print the summary of an earlier run")
    args = parser.parse_args()

    rows = load(args.out)
    if not args.summary_only:
        done = {row["file"] for row in rows}
        todo = [case for case in cases(args) if str(case[1]) not in done]
        if args.limit:
            todo = todo[: args.limit]
        if not todo and not rows:
            parser.error("no images found; pass --labelled, --normal, --tb, --sick, --not-chest or --side-view")
        new_file = not args.out.exists()
        columns = COLUMNS if new_file else header(args.out)
        with httpx.Client(base_url=args.url, timeout=60.0) as client, open(args.out, "a", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=columns, extrasaction="ignore")
            if new_file:
                writer.writeheader()
            for number, (set_name, path, expected) in enumerate(todo, 1):
                result = run_one(client, path, args.poll_seconds)
                row = {column: "" for column in COLUMNS} | {"set": set_name, "file": str(path), "expected": expected} | result
                writer.writerow(row)
                file.flush()
                rows.append({k: str(row.get(k, "")) for k in COLUMNS})
                print(f"[{number}/{len(todo)}] {path.name}: {expected} -> {result['outcome']}"
                      + (f", review {result['requires_review']}" if result["outcome"] == "read" else "")
                      + (f" ({result['conditions']})" if result.get("conditions") else ""))
    print()
    print(summarise(rows))


if __name__ == "__main__":
    main()
