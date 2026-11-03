"""File-backed control state shared by the dashboard and worker."""
import json
import os
import tempfile
import time
from pathlib import Path

DATA_DIR = Path(os.getenv("ANGELONE_DATA_DIR", str(Path(__file__).resolve().parent / "data"))).resolve()
os.environ["ANGELONE_DATA_DIR"] = str(DATA_DIR)
CONTROL_FILE = DATA_DIR / "control.json"
REPORT_FILE = DATA_DIR / "latest_report.json"


def read_json(path, default):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return default if isinstance(default, dict) and not isinstance(value, dict) else value
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(value, target, indent=2, allow_nan=False)
            target.write("\n")
        # Windows readers and virus scanners can briefly hold the destination
        # without FILE_SHARE_DELETE. Retain atomic replace and retry that race.
        for attempt in range(9):
            try:
                os.replace(temp_name, path)
                break
            except PermissionError:
                if attempt == 8:
                    raise
                time.sleep(min(.01 * 2 ** attempt, .1))
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def is_enabled():
    if not CONTROL_FILE.exists():
        return True
    return read_json(CONTROL_FILE, {}).get("enabled") is True


def set_enabled(enabled):
    write_json(CONTROL_FILE, {"enabled": bool(enabled)})

