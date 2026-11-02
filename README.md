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

## Live Angel One history

Create the four secret environment variables shown in `.env.example`. In the
managed cloud environment, add them as secret bindings; do not commit a filled
`.env` file. Then install and run:

```bash
python -m pip install -r requirements.txt
python fetch_history.py 3045 2026-01-01 2026-02-01
```

`3045` is only an example instrument token. Use the current Angel One token for
your chosen security. The command logs in with a time-based one-time password and
returns daily NSE candles as JSON. You can select another exchange or supported
interval with `--exchange` and `--interval`.

## Dashboard and continuous worker

Install dependencies, then start the background quote worker and dashboard:

```bash
python -m pip install -r requirements.txt
./start.sh
```

Open port 8501. The dashboard shows the latest quote and provides a persistent
Start/Stop analysis switch. The worker checks every 60 seconds by default. Set
`ANALYSIS_INTERVAL_SECONDS` to 30 or more. Configure `ANGELONE_SYMBOL`,
`ANGELONE_SYMBOL_TOKEN`, and `ANGELONE_EXCHANGE` for the target instrument.

The Dockerfile runs the dashboard and worker together. Mount persistent storage
at `/app/data` so the switch and latest report survive container restarts.

GitHub Actions creates a weekday report at 4:15 PM India time. Add the four
Angel One credentials as Actions secrets. Repository variables
`ANGELONE_SYMBOL` and `ANGELONE_SYMBOL_TOKEN` select the instrument.

## Current scope

Historical analysis, SmartAPI authentication, live quotes, the dashboard,
manual Start/Stop control, a continuous worker, and weekday reporting are
implemented. Live data remains inactive until cloud secrets are added. Cloud
hosting and instrument-token lookup are not yet configured.

## Next work

1. Confirm target instruments, data source, date range, and desired dashboard.
2. Configure API credentials through cloud secret bindings, never in Git.
3. Add an instrument master lookup so users can enter symbols instead of tokens.
4. Add time-zone handling, market-session checks, retries, and API rate limits.
5. Choose a hosting target and persistent data volume for continuous deployment.

Keep private market exports under `data/` (ignored by Git).

