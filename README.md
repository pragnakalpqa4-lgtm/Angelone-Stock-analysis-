# Angelone Stock Analysis

Initial historical-price analysis tool for the Angelone stock analysis project.
Uses Python 3.12+ with no third-party dependencies.

## Run in the cloud workspace

```bash
python --version
python -m unittest discover -s tests -v
python analysis.py examples/prices.csv --window 3
```

Use your own CSV with `date,close` columns. Dates must use YYYY-MM-DD,
with one positive finite closing price per trading day. Input is sorted by date;
duplicate dates are rejected. The example contains synthetic data.

The JSON report includes the latest observation-to-observation return, total
price return, simple moving average, and maximum peak-to-trough drawdown.
The moving average is null when fewer observations than the requested window
are available. Returns do not include dividends, transaction costs, or taxes.
Use consistently adjusted prices when comparing dates across corporate actions.

## Current scope

Historical CSV analysis and automated tests are implemented. Live Angel One
SmartAPI access, authentication, a dashboard, scheduling, and deployment are
not configured. No credentials are required for the current tool.

## Next work

1. Confirm target instruments, data source, date range, and desired dashboard.
2. Add an Angel One historical-data adapter with mocked API tests after confirming
   the current SmartAPI interface and account requirements.
3. Configure API credentials through cloud secret bindings, never in Git.
4. Add time-zone handling, market-session checks, retries, and API rate limits.
5. Choose a hosting target and data storage before adding scheduled refreshes.

Keep private market exports under `data/` (ignored by Git).
