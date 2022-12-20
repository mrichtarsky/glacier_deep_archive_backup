#!/usr/bin/env bash
# shellcheck disable=SC2034
set -euo pipefail

# The ZFS pool with data to backup
ZFS_POOL=tank

# A custom prefix that will be added to the name of the directory containing all
# backup files for this set, before the timestamp
PREFIX=data1

# Files and directories to backup (recursively), relative to the ZFS pool specified above.
# These wildcards can be used:
# - * for matching any number of chars, ? for matching one char.
# - [seq] matches any character in seq, [!seq] matches any character not in seq.
# - For a literal match, wrap the meta-characters in brackets.
#   For example, '[?]' matches the character '?'.
# - ** matches all directories recursively, including the current directory.
# Matching is case sensitive.

BACKUP_PATHS=(
    "file1.txt"  # Will backup /tank/file1.txt
    "pics"  # Top-level dir, recursively
    "sports/nba"  # Subdir, recursively
    "**/?"  # All files/dirs with a filename of length 1
    "projects/**/test.py"  # All test.py files in the projects subtree
    "*"  # All files in the pool
)

# The S3 bucket where data is stored
S3_BUCKET=your_s3_bucket

# The maximum size of uploaded files. Larger sizes increase the likelihood of upload
# failures and retries.
UPLOAD_LIMIT_MB=50000

# A path where the ZFS snapshot will be mounted during backup
SNAPSHOT_PATH=/snapshot_aws_backup

# Dir with at least UPLOAD_LIMIT_MB free space. A subdirectory 'backup_aws_buffer'
# will be DELETED and recreated there!
BUFFER_PATH_BASE='/tmp'
