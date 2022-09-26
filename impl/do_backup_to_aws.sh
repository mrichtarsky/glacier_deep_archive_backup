#!/usr/bin/env bash
set -euo pipefail

MODE=$1
SETTINGS=$2

if [[ "$MODE" == scratch ]]; then
    TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
    echo "Scratch backup, timestamp: $TIMESTAMP (needed if resume is necessary)"
else
    echo "Resuming"
    TIMESTAMP=$3
fi

# shellcheck disable=SC1090
source "$SETTINGS"

if [[ ! -s config/passphrase.txt ]]; then
    echo "Please define a passphrase in config/passphrase.txt!"
    exit 1
fi

SNAPSHOT=$ZFS_POOL@snapshot-aws-$TIMESTAMP
SET_PATH=state/sets
STATE_FILE=state/fs.state

if [[ "$MODE" != resume ]]; then
    rm -f state/resumable
    sudo zfs snapshot "$SNAPSHOT"
    sudo mkdir -p "$SNAPSHOT_PATH"
    sudo mount -t zfs -o ro "$SNAPSHOT" "$SNAPSHOT_PATH"
fi

BUFFER_PATH="$BUFFER_PATH_BASE/backup_aws_buffer"
rm -rf "$BUFFER_PATH"
mkdir -p "$BUFFER_PATH"

function cleanup()
{
    rm -rf "$BUFFER_PATH"
    if [[ ! -f state/resumable ]]; then
        echo "Resume not possible, destroying snapshot $SNAPSHOT"
        sudo umount "$SNAPSHOT_PATH"
        sudo zfs destroy "$SNAPSHOT"
    fi
}

trap cleanup EXIT

if [[ "$MODE" != resume ]]; then
    rm -f "$SET_PATH"/*
    mkdir -p "$SET_PATH"

    rm -f "$STATE_FILE"
fi

export SET_PATH SNAPSHOT_PATH STATE_FILE UPLOAD_LIMIT_MB ZFS_POOL

if [[ "$MODE" != resume ]]; then
    python impl/create_sets.py "${BACKUP_PATHS[@]}"
fi
touch state/resumable

export BUFFER_PATH S3_BUCKET TIMESTAMP
set +e
if python impl/upload_sets.py; then
    set -e
    rm state/resumable
    echo "Completed backup, timestamp $TIMESTAMP"
    echo "OK"
else
    echo
    echo "Error during processing. Keeping snapshot mounted at $SNAPSHOT_PATH. Please check for any errors that need to be fixed and run './backup_resume $SETTINGS $TIMESTAMP' to retry."
fi
