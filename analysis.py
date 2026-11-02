"""Historical closing-price analysis using Python's standard library."""
import argparse
import csv
import json
import math
from datetime import date
from pathlib import Path


def analyze(path, window=20):
    if window < 1:
        raise ValueError("Moving-average window must be positive")
    prices = {}
    with open(path, newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not {"date", "close"}.issubset(reader.fieldnames or []):
            raise ValueError("CSV requires date and close columns")
        for line, row in enumerate(reader, 2):
            try:
                day = date.fromisoformat(row["date"])
                close = float(row["close"])
                if not math.isfinite(close) or close <= 0:
                    raise ValueError("close must be finite and positive")
                if day in prices:
                    raise ValueError("duplicate date")
                prices[day] = close
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Invalid CSV row {line}: {exc}") from exc
    if len(prices) < 2:
        raise ValueError("At least two daily closing prices are required")
    days = sorted(prices)
    values = [prices[day] for day in days]
    peak = values[0]
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    return {
        "start_date": days[0].isoformat(),
        "end_date": days[-1].isoformat(),
        "observations": len(values),
        "latest_close": values[-1],
        "latest_return_pct": (values[-1] / values[-2] - 1) * 100,
        "total_return_pct": (values[-1] / values[0] - 1) * 100,
        "moving_average_window": window,
        "moving_average": sum(values[-window:]) / window if len(values) >= window else None,
        "max_drawdown_pct": drawdown * 100,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--window", type=int, default=20)
    args = parser.parse_args()
    try:
        result = analyze(args.csv_file, args.window)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
