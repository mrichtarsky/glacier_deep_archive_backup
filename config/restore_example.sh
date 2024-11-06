# shellcheck disable=SC2034
set -euo pipefail

# The S3 bucket where data is stored
S3_BUCKET=your_s3_bucket

# The subdirectory that was used for backup (see your backup config)
BUCKET_DIR=mydata1

# The timestamp of the backup to restore (see your bucket for which are available)
TIMESTAMP=2022-09-14-082857

# Standard or Bulk
# Standard takes up to 12 hours to restore, Bulk up to 48 hours, Bulk is 10x cheaper
RESTORE_TIER=Bulk

# Where to extract the archives to
EXTRACT_PATH=/tank_restore

# Dir with at least UPLOAD_LIMIT_MB free space. A subdirectory 'restore_aws_buffer'
# will be DELETED and recreated there!
BUFFER_PATH_BASE='/tmp'
