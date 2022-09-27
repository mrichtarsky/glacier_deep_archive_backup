#!/usr/bin/env bash
set -euo pipefail

SNAPSHOT_PATH=$1
FILE_LIST=$2
ARCHIVE=$3

tar -C "$SNAPSHOT_PATH" --create -v --verbatim-files-from "--files-from=$FILE_LIST" | zstd | gpg -c --cipher-algo AES256 --passphrase-file config/passphrase.txt --batch >"$ARCHIVE"
