#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 CONFIG_FILE"
    echo "  Example: $0 config/backup.sh"
    exit 1
fi

SETTINGS=$1

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH+=:$ROOT_DIR
export PYTHONUNBUFFERED=1

pushd "$ROOT_DIR" >/dev/null
trap 'popd >/dev/null' EXIT

MODE=$(basename "$0")

impl/do_backup_to_aws.sh "${MODE#backup_}" "$SETTINGS" 2>&1 | tee -i "logs/backup_$MODE.log"

echo "OK"
