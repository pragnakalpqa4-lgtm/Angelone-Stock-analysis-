# NIFTY 50 and SENSEX intraday analysis

Read-only index observations and descriptive analysis using Angel One data.
The fixed requirements are in [PROJECT_GOAL.md](PROJECT_GOAL.md).
All real and simulated orders are disabled. No order API is used by the service.

The repository now includes the local dual-index WebSocket collector rather
than defaulting the continuous worker to SBIN. Both indices must independently
meet data freshness checks. No synthetic prices are shown as broker observations.

## Run the service

Use Python 3.12 or newer:

```sh
python -m pip install -r requirements.txt
python service.py
```

Open http://127.0.0.1:8081 on the same machine. Without broker credentials,
the dashboard works and the pipeline reports `BLOCKED_AUTHENTICATION`.
It does not repeatedly attempt unauthenticated broker requests.

The service provides:

- Exactly NIFTY 50 / NSE and SENSEX / BSE subscriptions verified against the
  current official instrument master before collection.
- Actual observed one-minute candles, freshness checks, duplicate rejection,
  reconnect/resubscribe handling, and deterministic research specialists.
- A persistent Start/Stop control that starts or terminates the actual collector.
- A collector supervisor with bounded restart backoff and a stalled-process
  watchdog. Desired Start is displayed separately from actual worker status.
- A report at startup, followed by a dated daily report after 15:40 IST, including
  blocked and stopped states, plus a verified SQLite API backup.

Stopping analysis leaves diagnostics and reporting running so a stopped pipeline
can still explain its status. The emergency block is a separate research veto;
it cannot enable trading.

## Authentication and storage

Configure these through the hosting platform's protected secret management:

- `ANGELONE_API_KEY`
- `ANGELONE_CLIENT_CODE`
- `ANGELONE_PIN`
- `ANGELONE_TOTP_SECRET`

Never commit a filled `.env` file. Browser login does not configure the API.
The legacy `ANGEL_*` environment names remain supported for the existing Windows
setup. Windows DPAPI storage is local-user-bound and is not portable to Linux.
No entered credentials or broker exception bodies are logged by the collector.

`ANGELONE_DATA_DIR` defaults to this repository's `data/` directory. Mount it on
persistent storage for the market database, control state, reports and backups.
Relative configured paths are resolved once and shared with the child process.

## Continuous hosting

`deploy/angelone.service` is a systemd template for an existing Linux server.
`deploy/compose.yaml` supplies container restart handling and a persistent volume:

```sh
docker compose -f deploy/compose.yaml up -d --build
```

Compose requires a dashboard password and publishes port 8081 only on the host's
loopback interface. Use a private VPN or authenticated TLS reverse proxy for
remote access. Non-loopback application binding refuses startup without
`DASHBOARD_PASSWORD`. Optional `DASHBOARD_USER` defaults to `operator`.
The dashboard uses HTTP Basic authentication when a password is configured;
use TLS for every remote connection. These files prepare deployment; they do
not provision or prove an always-on server.

The parent service and child collector should be stopped as one process group
or container. systemd uses `KillMode=control-group`. Unsupervised Windows process
termination is not a validated cloud failover mechanism.

## Daily reports and delivery

The service writes JSON and Markdown to `data/reports/`. It generates a report
even with no prices or credentials. Backups go to `data/backups/` through
SQLite's backup API and pass `quick_check` before being marked complete.

The weekday GitHub Actions schedule remains 16:15 IST. It now retrieves the
deployed report rather than running a separate SBIN worker. Configure repository
variable `ANGELONE_REPORT_URL` with the service's HTTPS `/api/report` endpoint,
and matching `DASHBOARD_USER` / `DASHBOARD_PASSWORD` Actions secrets. The endpoint
must be reachable from the runner. Redirects and reports with the wrong scope,
date or enabled-order guard are rejected.

Without a deployed endpoint, Actions explicitly reports
`DEPLOYMENT_NOT_CONFIGURED`. Every run produces a job summary and downloadable
report artifact. That is not proof of chat, email or mobile notification delivery.
The existing Codex daily-update automation is managed separately and its
user-visible delivery still needs verification; this code does not create a
duplicate schedule or configure a messaging destination.

For an immediate report from the local persistent database:

```sh
python daily_report.py
```

## Verification

```sh
python -m unittest discover -s tests -v
node test_dashboard.cjs
```

Tests use isolated databases, mock broker transport, localhost HTTP requests and
real disposable subprocesses. They cover actual process Start/Stop, crash retry,
stalled collector recovery, fail-closed corrupt control, HTTP authorization and
origin checks, per-index freshness, reconnect epochs, backup persistence and
blocked reports. A passing test suite is not live broker or hosting evidence.

## Remaining operational limits

Protected broker authentication, actual dual-feed streaming and stable hosting
must be verified on the deployment target. The service does not provision paid
infrastructure, invent missing credentials, guarantee exchange availability,
perform unrestricted code changes, or run an autonomous LLM research agent.
Exchange holidays and within-minute coverage remain unverified. The descriptive
specialists do not constitute a validated trading strategy. Older simulator and
MCX helper tests remain regression coverage; neither is active service scope.

The old CSV/historical CLI remains available with
`pip install -r requirements-history.txt`; it is separate from the live service.

Protocol reference: [Angel One SmartWebSocketV2](https://github.com/angel-one/smartapi-python/blob/main/SmartApi/smartWebSocketV2.py).
Process and backup behavior follow [Python subprocess](https://docs.python.org/3/library/subprocess.html)
and [SQLite backup](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).
