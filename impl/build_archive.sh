#!/usr/bin/env bash
set -euo pipefail

FILE_LIST=$1
ARCHIVE=$2

tar --create -v --verbatim-files-from "--files-from=$FILE_LIST" | zstd | gpg -c --cipher-algo AES256 --passphrase-file config/passphrase.txt --batch >"$ARCHIVE"
