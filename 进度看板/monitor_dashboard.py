from __future__ import annotations

import argparse
import html
import json
import os
import re
import struct
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


YEARS = list(range(2013, 2025))
LOG_PATTERNS = {
    "cleaning": re.compile(r"Cleaning year (\d{4})"),
    "chunk": re.compile(r"Year (\d{4}) chunk \d+: raw rows processed = ([\d,]+)"),
    "before": re.compile(r"Year (\d{4}) before single-year dedup rows = ([\d,]+)"),
    "after": re.compile(r"Year (\d{4}) after single-year dedup rows = ([\d,]+)"),
    "complete": re.compile(
        r"Year (\d{4}) complete: raw=([\d,]+) before_dedup=([\d,]+) after_dedup=([\d,]+) elapsed_min=([\d.]+)"
    ),
    "skip": re.compile(r"Year (\d{4}) already exists, skipping"),
}


def workspace_root() -> Path:
    return Path(__file__).resolve().parents[1]


def find_dir(root: Path, kind: str) -> Path:
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if kind == "db" and child.name.startswith("数据库"):
            return child
        if kind == "out" and ("lite" in child.name.lower() or child.name.startswith("lite")):
            return child
    raise FileNotFoundError(f"Cannot find {kind} directory under {root}")


def find_year_file(root: Path, year: int) -> Path | None:
    direct = root / f"{year}.dta"
    nested = root / f"{year}.dta" / f"{year}.dta"
    if direct.is_file():
        return direct
    if nested.is_file():
        return nested
    matches = [p for p in root.rglob(f"{year}.dta") if p.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda p: p.stat().st_size, reverse=True)
    return matches[0]


def read_stata_nobs(path: Path) -> int | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(4096)
    except OSError:
        return None

    start = head.find(b"<N>")
    end = head.find(b"</N>")
    if start == -1 or end == -1 or end <= start:
        return None
    payload = head[start + 3 : end]
    if len(payload) == 8:
        return struct.unpack("<Q", payload)[0]
    if payload.strip().isdigit():
        return int(payload.strip())
    return None


def tail_lines(path: Path, max_bytes: int = 180_000) -> list[str]:
    if not path.exists():
        return []
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
            handle.readline()
        raw = handle.read()
    text = raw.decode("utf-8", errors="replace")
    return text.splitlines()


def file_info(path: Path | None) -> dict:
    if not path or not path.exists():
        return {"exists": False}
    stat = path.stat()
    return {
        "exists": True,
        "path": str(path),
        "sizeBytes": stat.st_size,
        "sizeGB": round(stat.st_size / (1024**3), 2),
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
    }


def parse_log(log_path: Path) -> tuple[dict[int, dict], list[str]]:
    states: dict[int, dict] = {year: {} for year in YEARS}
    recent = tail_lines(log_path)
    for line in recent:
        for key, pattern in LOG_PATTERNS.items():
            match = pattern.search(line)
            if not match:
                continue
            year = int(match.group(1))
            if year not in states:
                continue
            state = states[year]
            state["lastLogLine"] = line
            if key == "cleaning":
                state["seenRunning"] = True
            elif key == "chunk":
                state["processedRows"] = int(match.group(2).replace(",", ""))
                state["seenRunning"] = True
            elif key == "before":
                state["beforeDedup"] = int(match.group(2).replace(",", ""))
            elif key == "after":
                state["afterDedup"] = int(match.group(2).replace(",", ""))
            elif key == "complete":
                state["rawRows"] = int(match.group(2).replace(",", ""))
                state["beforeDedup"] = int(match.group(3).replace(",", ""))
                state["afterDedup"] = int(match.group(4).replace(",", ""))
                state["elapsedMin"] = float(match.group(5))
                state["completeInLog"] = True
                state["processedRows"] = state["rawRows"]
            elif key == "skip":
                state["skippedExisting"] = True
    return states, recent[-80:]


def build_status(root: Path, db_dir: Path, out_dir: Path) -> dict:
    log_path = out_dir / "patent_python_cleaning.log"
    log_states, recent = parse_log(log_path)
    year_items = []

    total_rows = 0
    completed_rows = 0
    current = None

    for year in YEARS:
        raw_file = find_year_file(db_dir, year)
        output_file = out_dir / f"patent_{year}_lite.dta"
        tmp_file = out_dir / f"patent_{year}_lite.tmp.dta"
        nobs = read_stata_nobs(raw_file) if raw_file else None
        if nobs:
            total_rows += nobs

        state = log_states.get(year, {})
        output_exists = output_file.exists()
        tmp_exists = tmp_file.exists()
        processed = state.get("processedRows", 0)

        if output_exists or state.get("completeInLog") or state.get("skippedExisting"):
            status = "completed"
            processed = nobs or state.get("rawRows") or processed
            percent = 100.0
            if nobs:
                completed_rows += nobs
        elif state.get("seenRunning") or tmp_exists:
            status = "running"
            percent = (processed / nobs * 100) if nobs else 0.0
            if current is None:
                current = year
            if nobs:
                completed_rows += min(processed, nobs)
        else:
            status = "pending"
            percent = 0.0

        item = {
            "year": year,
            "status": status,
            "percent": round(max(0.0, min(percent, 100.0)), 2),
            "processedRows": processed,
            "totalRows": nobs,
            "beforeDedup": state.get("beforeDedup"),
            "afterDedup": state.get("afterDedup"),
            "elapsedMin": state.get("elapsedMin"),
            "rawFile": file_info(raw_file),
            "outputFile": file_info(output_file),
            "tempFile": file_info(tmp_file),
            "lastLogLine": state.get("lastLogLine"),
        }
        year_items.append(item)

    if current is None:
        for item in year_items:
            if item["status"] != "completed":
                current = item["year"]
                break

    overall_percent = (completed_rows / total_rows * 100) if total_rows else 0.0
    current_item = next((item for item in year_items if item["year"] == current), None)
    return {
        "workspace": str(root),
        "dbDir": str(db_dir),
        "outDir": str(out_dir),
        "logPath": str(log_path),
        "logExists": log_path.exists(),
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "currentYear": current,
        "current": current_item,
        "overallPercent": round(overall_percent, 2),
        "completedYears": sum(1 for item in year_items if item["status"] == "completed"),
        "totalYears": len(year_items),
        "totalRows": total_rows,
        "completedRows": completed_rows,
        "years": year_items,
        "recentLog": recent,
    }


class DashboardHandler(SimpleHTTPRequestHandler):
    root = workspace_root()
    db_dir = find_dir(root, "db")
    out_dir = find_dir(root, "out")
    static_dir = Path(__file__).resolve().parent / "static"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self.send_json(build_status(self.root, self.db_dir, self.out_dir))
            return
        if parsed.path == "/":
            self.path = "/index.html"
        return super().do_GET()

    def translate_path(self, path: str) -> str:
        parsed = urlparse(path)
        requested = parsed.path.lstrip("/") or "index.html"
        safe = Path(*[part for part in requested.split("/") if part and part not in {".", ".."}])
        return str(self.static_dir / safe)

    def send_json(self, data: dict) -> None:
        payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args) -> None:
        message = "%s - %s" % (self.address_string(), format % args)
        print(html.unescape(message))


def main() -> int:
    parser = argparse.ArgumentParser(description="Local dashboard for patent cleaning progress.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Progress dashboard: {url}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
