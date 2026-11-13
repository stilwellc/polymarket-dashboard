#!/bin/bash
# Starts the P&L dashboard on this Mac only, then open http://127.0.0.1:8765
cd "$(dirname "$0")" && exec .venv/bin/python -m pmdash "$@"
