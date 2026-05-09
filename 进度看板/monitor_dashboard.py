from __future__ import annotations

import argparse
import html
import json
import os
import re
import struct
import subprocess
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
STALE_AFTER_SECONDS = 180


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


def parts_dir_for(out_dir: Path, year: int) -> Path:
    return out_dir / f"patent_{year}_lite_parts"


def parts_manifest_for(out_dir: Path, year: int) -> Path:
    return parts_dir_for(out_dir, year) / "manifest.json"


def partition_info(out_dir: Path, year: int) -> dict:
    parts_dir = parts_dir_for(out_dir, year)
    manifest = parts_manifest_for(out_dir, year)
    if not manifest.exists():
        return {"exists": False}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    part_files = sorted(parts_dir.glob("patent_*_lite_part*.dta"))
    total_size = sum(path.stat().st_size for path in part_files if path.exists())
    return {
        "exists": True,
        "path": str(parts_dir),
        "partCount": data.get("partCount") or len(part_files),
        "totalRows": data.get("afterDedup") or data.get("totalRows"),
        "sizeBytes": total_size,
        "sizeGB": round(total_size / (1024**3), 2),
        "modified": datetime.fromtimestamp(manifest.stat().st_mtime).isoformat(timespec="seconds"),
    }


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


def log_sources(out_dir: Path) -> list[Path]:
    sources = [out_dir / "patent_python_cleaning.log"]
    sources.extend(sorted(out_dir.glob("python_cleaning_*_stdout.log")))
    return [path for path in sources if path.exists()]


def combined_log_lines(out_dir: Path) -> list[str]:
    lines: list[str] = []
    for path in log_sources(out_dir):
        lines.extend(tail_lines(path))

    def timestamp(line: str) -> str:
        return line[:23] if len(line) >= 23 and line[:4].isdigit() else ""

    lines.sort(key=timestamp)
    return lines[-240:]


def latest_log_time(lines: list[str]) -> float | None:
    for line in reversed(lines):
        if len(line) < 23 or not line[:4].isdigit():
            continue
        try:
            return datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f").timestamp()
        except ValueError:
            continue
    return None


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


def load_runtime_status(out_dir: Path) -> dict:
    path = out_dir / "patent_cleaning_status.json"
    if not path.exists():
        return {"exists": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"exists": True, "error": str(exc), "path": str(path)}
    data["exists"] = True
    data["path"] = str(path)
    return data


def cleaner_script(root: Path) -> Path:
    matches = [p for p in root.rglob("build_patent_lite_feature_py.py") if p.is_file()]
    if not matches:
        raise FileNotFoundError("Cannot find build_patent_lite_feature_py.py")
    matches.sort(key=lambda p: len(p.parts))
    return matches[0]


def cleaner_processes() -> list[dict]:
    command = r"""
$items = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*build_patent_lite_feature_py.py*' }
if ($items) {
  $items | ForEach-Object {
    $proc = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
    [pscustomobject]@{
      ProcessId = $_.ProcessId
      Name = $_.Name
      CommandLine = $_.CommandLine
      CPU = if ($proc) { $proc.CPU } else { $null }
      WorkingSet64 = if ($proc) { $proc.WorkingSet64 } else { $null }
      PrivateMemorySize64 = if ($proc) { $proc.PrivateMemorySize64 } else { $null }
      StartTime = if ($proc -and $proc.StartTime) { $proc.StartTime.ToString("s") } else { $null }
    }
  } | ConvertTo-Json -Compress
}
"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception:
        return []
    output = result.stdout.strip()
    if not output:
        return []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = [data]
    return [
        {
            "pid": item.get("ProcessId"),
            "name": item.get("Name"),
            "commandLine": item.get("CommandLine"),
            "cpuSeconds": item.get("CPU"),
            "workingSetBytes": item.get("WorkingSet64"),
            "privateBytes": item.get("PrivateMemorySize64"),
            "startTime": item.get("StartTime"),
        }
        for item in data
        if item.get("ProcessId")
    ]


def stop_cleaner_processes() -> dict:
    processes = cleaner_processes()
    stopped = []
    for process in processes:
        pid = process["pid"]
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        stopped.append(pid)
    return {"ok": True, "stopped": stopped}


def next_incomplete_year(out_dir: Path) -> int | None:
    for year in YEARS:
        if not (out_dir / f"patent_{year}_lite.dta").exists() and not parts_manifest_for(out_dir, year).exists():
            return year
    return None


def start_cleaner(root: Path, out_dir: Path) -> dict:
    existing = cleaner_processes()
    if existing:
        return {"ok": True, "alreadyRunning": True, "processes": existing}

    start_year = next_incomplete_year(out_dir)
    if start_year is None:
        return {"ok": False, "message": "All years already have output files."}

    script = cleaner_script(root)
    stdout_path = out_dir / f"python_cleaning_{start_year}_stdout.log"
    stderr_path = out_dir / f"python_cleaning_{start_year}_stderr.log"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            [
                "python",
                "-u",
                str(script),
                "--start-year",
                str(start_year),
                "--end-year",
                str(YEARS[-1]),
                "--engine",
                "streaming",
            ],
            cwd=root,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
    return {"ok": True, "started": process.pid, "startYear": start_year}


def parse_log_lines(lines: list[str]) -> tuple[dict[int, dict], list[str]]:
    states: dict[int, dict] = {year: {} for year in YEARS}
    for line in lines:
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
                states[year] = {"seenRunning": True, "lastLogLine": line, "phase": "starting"}
            elif key == "chunk":
                state["processedRows"] = int(match.group(2).replace(",", ""))
                state["seenRunning"] = True
                state["phase"] = "reading"
            elif key == "before":
                state["beforeDedup"] = int(match.group(2).replace(",", ""))
                state["phase"] = "deduplicating"
            elif key == "after":
                state["afterDedup"] = int(match.group(2).replace(",", ""))
                state["phase"] = "writing"
            elif key == "complete":
                state["rawRows"] = int(match.group(2).replace(",", ""))
                state["beforeDedup"] = int(match.group(3).replace(",", ""))
                state["afterDedup"] = int(match.group(4).replace(",", ""))
                state["elapsedMin"] = float(match.group(5))
                state["completeInLog"] = True
                state["processedRows"] = state["rawRows"]
                state["phase"] = "complete"
            elif key == "skip":
                state["skippedExisting"] = True
    return states, lines[-80:]


def build_status(root: Path, db_dir: Path, out_dir: Path) -> dict:
    log_path = out_dir / "patent_python_cleaning.log"
    sources = log_sources(out_dir)
    combined_lines = combined_log_lines(out_dir)
    log_states, recent = parse_log_lines(combined_lines)
    processes = cleaner_processes()
    runtime_status = load_runtime_status(out_dir)
    cleaner_running = len(processes) > 0
    now_ts = time.time()
    log_stale_seconds = None
    newest_log_ts = latest_log_time(combined_lines)
    if newest_log_ts is not None:
        log_stale_seconds = max(0, int(now_ts - newest_log_ts))
    elif sources:
        log_stale_seconds = max(0, int(now_ts - max(path.stat().st_mtime for path in sources)))
    if runtime_status.get("exists") and runtime_status.get("updatedAt"):
        try:
            status_ts = datetime.strptime(str(runtime_status["updatedAt"]), "%Y-%m-%d %H:%M:%S").timestamp()
            status_age = max(0, int(now_ts - status_ts))
            if log_stale_seconds is None or status_age < log_stale_seconds:
                log_stale_seconds = status_age
        except ValueError:
            pass
    year_items = []

    total_rows = 0
    completed_rows = 0
    current = None

    for year in YEARS:
        raw_file = find_year_file(db_dir, year)
        output_file = out_dir / f"patent_{year}_lite.dta"
        tmp_file = out_dir / f"patent_{year}_lite.tmp.dta"
        parts = partition_info(out_dir, year)
        nobs = read_stata_nobs(raw_file) if raw_file else None
        if nobs:
            total_rows += nobs

        state = log_states.get(year, {})
        output_exists = output_file.exists()
        tmp_exists = tmp_file.exists()
        processed = state.get("processedRows", 0)

        if output_exists or parts["exists"] or state.get("completeInLog") or state.get("skippedExisting"):
            status = "completed"
            processed = nobs or state.get("rawRows") or processed
            percent = 100.0
            if nobs:
                completed_rows += nobs
        elif state.get("seenRunning") or tmp_exists:
            status = "running" if cleaner_running else "stopped"
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
            "runtimePhase": state.get("phase"),
            "runtimeMessage": None,
            "rawFile": file_info(raw_file),
            "outputFile": file_info(output_file),
            "partitionedOutput": parts,
            "tempFile": file_info(tmp_file),
            "lastLogLine": state.get("lastLogLine"),
        }
        if runtime_status.get("exists") and runtime_status.get("year") == year:
            item["runtimePhase"] = runtime_status.get("phase")
            item["runtimeMessage"] = runtime_status.get("message")
            item["runtimeStatus"] = runtime_status
            if runtime_status.get("processedRows") is not None and not output_exists:
                item["processedRows"] = runtime_status.get("processedRows")
                processed = item["processedRows"]
                item["percent"] = round(max(0.0, min((processed / nobs * 100) if nobs else 0.0, 100.0)), 2)
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
        "logSources": [str(path) for path in sources],
        "cleanerRunning": cleaner_running,
        "cleanerProcesses": processes,
        "runtimeStatus": runtime_status,
        "logStaleSeconds": log_stale_seconds,
        "logStale": bool(cleaner_running and log_stale_seconds is not None and log_stale_seconds > STALE_AFTER_SECONDS),
        "staleAfterSeconds": STALE_AFTER_SECONDS,
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

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/control":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        action = payload.get("action")
        if action == "start":
            self.send_json(start_cleaner(self.root, self.out_dir))
            return
        if action == "stop":
            self.send_json(stop_cleaner_processes())
            return
        self.send_error(400, "Unknown action")

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
