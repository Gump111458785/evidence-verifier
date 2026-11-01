#!/bin/sh
set -eu
cd "$(dirname "$0")"
exec "${EV_PYTHON:-python3}" server.py "$@"
