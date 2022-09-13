#!/usr/bin/env bash
# shellcheck disable=SC2034
set -euo pipefail

# The ZFS pool with data to backup
ZFS_POOL=tank

# Files and directories to backup (recursively)
BACKUP_PATHS=(
    "file1.txt"
    "pics"
    "sports/nba"
)

# The S3 bucket where data is stored
S3_BUCKET=your_s3_bucket

# The maximum size of uploaded files. Larger sizes increase the likelihood of upload failures and retries.
UPLOAD_LIMIT_MB=50000

# A path where the ZFS snapshot will be mounted during backup
SNAPSHOT_PATH=/snapshot_aws_backup

# Dir with at least UPLOAD_LIMIT_MB free space. A subdirectory 'backup_aws_buffer' will be DELETED and recreated there!
BUFFER_PATH_BASE='/tmp'
