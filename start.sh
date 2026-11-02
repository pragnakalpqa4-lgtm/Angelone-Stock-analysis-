#!/bin/sh
set -eu
python worker.py &
worker_pid=$!
trap 'kill "$worker_pid" 2>/dev/null || true' EXIT INT TERM
exec streamlit run dashboard.py --server.address=0.0.0.0 --server.port="${PORT:-8501}"
