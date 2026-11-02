"""Continuously fetch a live Angel One quote while the system is enabled."""
import argparse
import os
import time
from datetime import datetime, timezone

from angelone import AngelOneClient, Settings
from runtime_state import REPORT_FILE, is_enabled, write_json


def configuration():
    target = {
        "symbol": os.getenv("ANGELONE_SYMBOL", "SBIN-EQ"),
        "symbol_token": os.getenv("ANGELONE_SYMBOL_TOKEN", "3045"),
        "exchange": os.getenv("ANGELONE_EXCHANGE", "NSE"),
    }
    interval = int(os.getenv("ANALYSIS_INTERVAL_SECONDS", "60"))
    if interval < 30:
        raise ValueError("ANALYSIS_INTERVAL_SECONDS must be at least 30")
    return target, interval


def run_once():
    target, _ = configuration()
    checked_at = datetime.now(timezone.utc).isoformat()
    if not is_enabled():
        report = {"status": "stopped", "checked_at": checked_at, **target}
    else:
        try:
            client = AngelOneClient(Settings.from_env())
            client.login()
            report = {
                "status": "running", "checked_at": checked_at,
                **client.quote(**target),
            }
        except Exception as exc:
            report = {
                "status": "error", "checked_at": checked_at,
                **target, "error": str(exc),
            }
    write_json(REPORT_FILE, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    _, interval = configuration()
    while True:
        print(run_once(), flush=True)
        if args.once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()
