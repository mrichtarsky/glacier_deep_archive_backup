import json
import os
import subprocess


class BackupException(Exception):
    pass

def size_to_unit(size):
    KiB = 1024
    MiB = 1024*KiB
    GiB = 1024*MiB
    TiB = 1024*GiB
    PiB = 1024*TiB
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
        result = f"{size / factor:.2f}"
    else:
        result = f"{size}"
    if unit is not None:
        result += f" {unit}"
    return result

def size_to_string(size):
    factor, unit = size_to_unit(size)
    return size_to_string_factor(size, factor, unit)

def clean_multipart_uploads(s3_bucket):
    cmd = ('aws', 's3api', 'list-multipart-uploads', '--bucket', s3_bucket)
    parts_json = subprocess.check_output(cmd)
    if len(parts_json) > 0:
        parts = json.loads(parts_json)
        for upload in parts['Uploads']:
            print(f"Cleaning remaining multipart {upload['Key']}")
            cmd = ('aws', 's3api', 'abort-multipart-upload', '--bucket',
                   s3_bucket, '--key', upload['Key'], '--upload-id',
                   upload['UploadId'])
            subprocess.run(cmd, check=True)

def make_set_info_filename(list_file):
    info_file = os.path.splitext(list_file)[0] + '.info'
    return info_file

if __name__ == '__main__':
    for i in (0, 1, 1024, 1024**2, 1024**3, 1024**4, 1024**5):
        print(f"{i}: {size_to_string(i)}")
