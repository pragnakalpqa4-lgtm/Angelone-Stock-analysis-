#!/bin/sh
set -eu
exec python service.py --host "${DASHBOARD_HOST:-127.0.0.1}" --port "${PORT:-8081}"
