#!/bin/bash
cd /home/ubuntu/.wine/drive_c/mt5bot
export HF_HOME=/tmp/hf_kronos
exec /tmp/kronos_venv/bin/python kronos_runtime/daemon.py
