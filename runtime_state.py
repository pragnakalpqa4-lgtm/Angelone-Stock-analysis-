"""File-backed control state shared by the dashboard and worker."""
import json
import os
import tempfile
from pathlib import Path

DATA_DIR = Path(os.getenv("ANGELONE_DATA_DIR", "data"))
CONTROL_FILE = DATA_DIR / "control.json"
REPORT_FILE = DATA_DIR / "latest_report.json"


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(value, target, indent=2, allow_nan=False)
            target.write("\n")
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def is_enabled():
    return bool(read_json(CONTROL_FILE, {"enabled": True}).get("enabled", True))


def set_enabled(enabled):
    write_json(CONTROL_FILE, {"enabled": bool(enabled)})
