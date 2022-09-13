#!/usr/bin/env bash
set -euo pipefail

FILE_LIST=$1
ARCHIVE=$2

tar --create --file=- -v --verbatim-files-from "--files-from=$FILE_LIST" --zstd | openssl enc -e -aes256 -kfile config/passphrase.txt -pbkdf2 -out "$ARCHIVE"
