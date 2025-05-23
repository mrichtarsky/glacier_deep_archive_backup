#!/usr/bin/env bash
set -euo pipefail

MODE=$1

RESUME_FILE=state/resume_info

if [[ "$MODE" == scratch ]]; then
    SETTINGS=$(realpath "$2")
    TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
    echo "Scratch backup"
elif [[ "$MODE" == resume ]]; then
    echo "Resuming"
    if [[ ! -s "$RESUME_FILE" ]]; then
        echo "Not able to resume, please run ./backup_scratch first!"
        exit 1
    fi

    source "$RESUME_FILE"
elif [[ "$MODE" == duplicity_full ]]; then
    SETTINGS=$(realpath "$2")
    TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
    echo "Full duplicity backup"
elif [[ "$MODE" == duplicity_incremental ]]; then
    SETTINGS=$(realpath "$2")
    TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
    echo "Incremental duplicity backup"
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
        echo "Error or cancel during processing. Not destroying snapshot" \
            "($SNAPSHOT). Please check for any errors that need to be fixed" \
            "and run './backup_resume' to retry. If you do not want to" \
            "resume, please destroy the snapshot manually."
    else
        echo "Destroying snapshot $SNAPSHOT"
        sudo zfs destroy "$SNAPSHOT"
    fi
}

if [[ "$MODE" == scratch ]] || [[ "$MODE" == duplicity_full ]] || [[ "$MODE" == duplicity_incremental ]]; then
    rm -f "$RESUME_FILE"
    rm -f "$SET_PATH"/*
    mkdir -p "$SET_PATH"
    rm -f "$STATE_FILE"

    sudo zfs snapshot "$SNAPSHOT"
fi

sudo mkdir -p "$SNAPSHOT_PATH"
sudo mount -t zfs -o ro "$SNAPSHOT" "$SNAPSHOT_PATH"
trap cleanup EXIT

if [[ "$MODE" == duplicity_full ]]; then
    export BUCKET_DIR BUFFER_PATH S3_BUCKET SEAL_ACTION SNAPSHOT_PATH

    impl/duplicity_backup.py full "${BACKUP_PATHS[@]}"
elif [[ "$MODE" == duplicity_incremental ]]; then
    export BUCKET_DIR BUFFER_PATH S3_BUCKET SEAL_ACTION SNAPSHOT_PATH

    impl/duplicity_backup.py incremental "${BACKUP_PATHS[@]}"
else
    export SET_PATH SETTINGS SNAPSHOT_PATH STATE_FILE UPLOAD_LIMIT_MB SEAL_ACTION ZFS_POOL

    if [[ "$MODE" == scratch ]]; then
        impl/create_sets.py "${BACKUP_PATHS[@]}"
        echo -e "SETTINGS=\"$SETTINGS\"\\nTIMESTAMP=\"$TIMESTAMP\"" >"$RESUME_FILE"
    fi

    export BUCKET_DIR BUFFER_PATH S3_BUCKET TIMESTAMP
    impl/upload_sets.py
    rm "$RESUME_FILE"
fi


echo "Completed backup (config=$SETTINGS, timestamp=$TIMESTAMP)"
