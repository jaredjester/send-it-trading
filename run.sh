#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
set -a; source "$DIR/.env" 2>/dev/null; set +a
exec ${PYTHON:-python3} "$@"
