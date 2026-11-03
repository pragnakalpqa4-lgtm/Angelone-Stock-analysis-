"""Fetch Angel One candle history and print it as JSON."""
import argparse
import json
from datetime import datetime

from angelone import AngelOneClient, Settings


def parse_datetime(value):
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use YYYY-MM-DD or YYYY-MM-DDTHH:MM") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol_token", help="Angel One instrument token")
    parser.add_argument("start", type=parse_datetime)
    parser.add_argument("end", type=parse_datetime)
    parser.add_argument("--exchange", default="NSE")
    parser.add_argument("--interval", default="ONE_DAY")
    args = parser.parse_args()
    try:
        client = AngelOneClient(Settings.from_env())
        client.login()
        rows = client.historical(
            args.symbol_token,
            args.start,
            args.end,
            exchange=args.exchange,
            interval=args.interval,
        )
    except (ValueError, RuntimeError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()

