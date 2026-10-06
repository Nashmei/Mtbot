#!/bin/bash
set -e
ROOT=/home/ubuntu/.wine/drive_c/mt5bot
mkdir -p "$ROOT/kronos_runtime/data" "$ROOT/kronos_runtime/signals"
WP=/home/ubuntu/.wine/drive_c/users/ubuntu/AppData/Local/Programs/Python/Python311/python.exe
for S in XAUUSD.sd EURUSD.sd; do
  /opt/wine-staging/bin/wine "$WP" "$ROOT/kronos_runtime/export_m5.py" "$S" "$ROOT/kronos_runtime/data/${S}_M5.csv"
  "$ROOT/kronos_runtime/venv/bin/python" "$ROOT/kronos_runtime/worker.py" --input "$ROOT/kronos_runtime/data/${S}_M5.csv" --symbol "$S" --out "$ROOT/kronos_runtime/signals/${S}_M5.json"
done
