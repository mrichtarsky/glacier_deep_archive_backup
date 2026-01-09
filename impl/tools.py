import json
import os
import pathlib
import subprocess
import sys

NO_BACKUP_MARKER = '.NO_BACKUP'
GDAB_SEALED_MARKER = '.GDAB_SEALED'


class BackupException(Exception):
    pass


def size_to_unit(size):
    KiB = 1024
    MiB = 1024 * KiB
    GiB = 1024 * MiB
    TiB = 1024 * GiB
    PiB = 1024 * TiB
    if size >= PiB:
        return PiB, 'PiB'
    if size >= TiB:
        return TiB, 'TiB'
    if size >= GiB:
        return GiB, 'GiB'
    if size >= MiB:
        return MiB, 'MiB'
    if size >= KiB:
        return KiB, 'KiB'
    return 1, 'B'


def size_to_string_factor(size, factor, unit=None):
    result = ''
    if factor > 1:
        result = f'{size / factor:.2f}'
    else:
        result = f'{size}'
    if unit is not None:
        result += f' {unit}'
    return result


def size_to_string(size):
    factor, unit = size_to_unit(size)
    return size_to_string_factor(size, factor, unit)


def glob_backup_paths(backup_paths_unglobbed, snapshot_path):
    backup_paths = []
    num_warnings = 0
    base_path = pathlib.Path(snapshot_path)
    for path_unglobbed in backup_paths_unglobbed:
        paths_globbed = tuple(base_path.glob(path_unglobbed))
        if len(paths_globbed) == 0:
            print(f'WARNING: Path {path_unglobbed} does not exist and will be ignored!')
            num_warnings += 1
        for path_globbed in paths_globbed:
            backup_paths.append(os.fspath(path_globbed.relative_to(base_path)))
    return backup_paths, num_warnings


def glob_backup_paths_and_check(backup_paths_unglobbed, snapshot_path):
    backup_paths, num_warnings = glob_backup_paths(backup_paths_unglobbed,
                                                   snapshot_path)

    if num_warnings > 0 and os.environ.get('IGNORE_WARNINGS') != '1':
        res = input(f'{num_warnings} warning(s) encountered, see above.'
                    ' Proceed? (IGNORE_WARNINGS=1 skips this) [y/n] ').strip().lower()
        if res != 'y':
            print('ABORTED')
            sys.exit(1)
    return backup_paths


def clean_multipart_uploads(s3_bucket):
    cmd = ('aws', 's3api', 'list-multipart-uploads', '--bucket', s3_bucket)
    parts_json = subprocess.check_output(cmd)
    if len(parts_json) > 0:
        parts = json.loads(parts_json)
        for upload in parts['Uploads']:
            print(f"Cleaning remaining multipart {upload['Key']}")
            cmd = ('aws', 's3api', 'abort-multipart-upload', '--bucket', s3_bucket,
                   '--key', upload['Key'], '--upload-id', upload['UploadId'])
            subprocess.run(cmd, check=True)


def make_set_info_filename(list_file):
    info_file = os.path.splitext(list_file)[0] + '.info'
    return info_file


def normalize_bucket_dir(bucket_dir):
    # Avoid extraneous directories on S3, normalize path
    bucket_dir = bucket_dir.strip('/')
    if len(bucket_dir):
        bucket_dir += '/'
    return bucket_dir


class SealAction():
    SEAL_AFTER_BACKUP, SKIP_SEALED = range(2)

    def __init__(self):
        seal_action_str = os.environ.get('SEAL_ACTION', 'disable')
        try:
            self.action = {'seal_after_backup': SealAction.SEAL_AFTER_BACKUP,
                           'skip_sealed': SealAction.SKIP_SEALED,
                           'disable': None}[seal_action_str.lower()]
        except KeyError:
            # pylint: disable=raise-missing-from
            raise BackupException(f'Invalid seal action {seal_action_str}')

    def is_seal_after_backup(self):
        return self.action == SealAction.SEAL_AFTER_BACKUP

    def is_skip_sealed(self):
        return self.action == SealAction.SKIP_SEALED


if __name__ == '__main__':
    for i in (0, 1, 1024, 1024**2, 1024**3, 1024**4, 1024**5):
        print(f'{i}: {size_to_string(i)}')
