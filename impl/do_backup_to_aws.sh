#!/usr/bin/env bash
set -euo pipefail

MODE=$1
SETTINGS=$2

RESUME_FILE=state/resumable

if [[ "$MODE" == scratch ]]; then
    TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
    echo "Scratch backup, timestamp: $TIMESTAMP (needed if resume is necessary)"
elif [[ "$MODE" == resume ]]; then
    echo "Resuming"
    TIMESTAMP=$(cat "$RESUME_FILE")
else
    echo "Invalid mode argument: $MODE"
    exit 1
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

BUFFER_PATH="$BUFFER_PATH_BASE/backup_aws_buffer"
rm -rf "$BUFFER_PATH"
mkdir -p "$BUFFER_PATH"

function cleanup()
{
    rm -rf "$BUFFER_PATH"
    sudo umount "$SNAPSHOT_PATH" || true
    if [[ -f "$RESUME_FILE" ]]; then
        echo
        echo "Error or cancel during processing. Not destroying snapshot." \
            "Please check for any errors that need to be fixed and run" \
            "'./backup_resume $SETTINGS $TIMESTAMP' to retry."
    else
        echo "Destroying snapshot $SNAPSHOT"
        sudo zfs destroy "$SNAPSHOT"
    fi
}

if [[ "$MODE" == scratch ]]; then
    rm -f "$RESUME_FILE"
    rm -f "$SET_PATH"/*
    mkdir -p "$SET_PATH"
    rm -f "$STATE_FILE"

    sudo zfs snapshot "$SNAPSHOT"
fi

sudo mkdir -p "$SNAPSHOT_PATH"
sudo mount -t zfs -o ro "$SNAPSHOT" "$SNAPSHOT_PATH"
trap cleanup EXIT

export SET_PATH SNAPSHOT_PATH STATE_FILE UPLOAD_LIMIT_MB ZFS_POOL

if [[ "$MODE" == scratch ]]; then
    impl/create_sets.py "${BACKUP_PATHS[@]}"
fi
echo -n "$TIMESTAMP" >"$RESUME_FILE"

export BUCKET_DIR BUFFER_PATH S3_BUCKET TIMESTAMP
impl/upload_sets.py
rm "$RESUME_FILE"

echo "Completed backup (config=$SETTINGS, timestamp=$TIMESTAMP)"
