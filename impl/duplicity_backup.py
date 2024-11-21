#!/usr/bin/env python
'''
Ran by do_backup_to_aws.sh

Parameters passed by environment, backup mode and backup paths on command line.

SNAPSHOT_PATH is where the ZFS snapshot is mounted.
'''

import os
import subprocess
import sys

from impl.tools import (GDAB_SEALED_MARKER, BackupException, SealAction,
                        glob_backup_paths_and_check)

snapshot_path = os.path.normpath(os.environ['SNAPSHOT_PATH'])
buffer_path = os.environ['BUFFER_PATH']
s3_bucket = os.environ['S3_BUCKET']
bucket_dir = os.environ['BUCKET_DIR']

mode = sys.argv[1]
if mode not in ('full', 'incremental'):
    raise BackupException(f'Invalid {mode=}')

backup_paths_unglobbed = tuple(map(os.path.normpath, sys.argv[2:]))
backup_paths = glob_backup_paths_and_check(backup_paths_unglobbed, snapshot_path)

with open('config/passphrase.txt', 'rt') as f:
    os.environ['PASSPHRASE'] = f.readline().rstrip()

bucket_dir_escaped = bucket_dir.rstrip('/').replace('_', '__').replace('/', '_')
name = f'{s3_bucket}_{bucket_dir_escaped}'
cmd = ['duplicity', f'--name={name}', '--filter-literal']

if SealAction().is_skip_sealed():
    cmd.append(f'--exclude-if-present={GDAB_SEALED_MARKER}')

prefix = '.'
for backup_path in backup_paths:
    backup_path_prefixed = os.path.join(prefix, backup_path)
    cmd.append(f'--include={backup_path_prefixed}')

s3_url = f's3://{s3_bucket}/{bucket_dir}'
cmd.extend([
    '--filter-globbing',
    f'--exclude={os.path.join(prefix, "*")}',
    f'--tempdir={buffer_path}',
    '--volsize=10000',
    '--concurrency=2',
    '--s3-use-deep-archive',
    '--verbosity=5',  # Smallest verbosity that shows files
    mode,
    prefix,
    s3_url
])

print(f"Running '{' '.join(cmd)}'")
subprocess.run(cmd, check=True, cwd=snapshot_path)

print(f'Success, restore URL for duplicity: {s3_url}')
