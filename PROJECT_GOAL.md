# Project Goal

Build an always-on, read-only intraday market-analysis system for **NIFTY 50**
and **SENSEX** using Angel One market data.

## Required outcome

1. Show NIFTY 50 and SENSEX intraday data on one dashboard.
2. Continuously analyse the market during Indian market hours.
3. Display data freshness, trend/analysis status, system health, and errors.
4. Generate and deliver an automatic daily report without waiting for a user
   prompt.
5. Provide one master Start/Stop switch so the user can manually pause or resume
   all analysis.
6. Run from stable cloud infrastructure and recover cleanly after restarts.
7. Store credentials only in secret management; never in Git or dashboard code.
8. Monitor failures and clearly report when data is stale or unavailable.

## Safety boundary

The system performs analysis only. It must not place, modify, or cancel trades
unless the user separately requests and explicitly authorizes trading features.

## Instruments

- NIFTY 50
- SENSEX

Do not replace these with individual stocks or other indices unless the user
explicitly changes the project scope.

## Definition of done

- Live Angel One data is verified for both indices.
- Dashboard is reachable through a stable URL.
- Intraday analysis runs automatically during market hours.
- Start/Stop control affects the complete analysis pipeline.
- Daily reports are generated and delivered automatically.
- Health checks, stale-data detection, logs, and meaningful tests pass.

