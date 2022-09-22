import json
import subprocess


class BackupException(Exception):
    pass

def size_to_string(size):
    KiB = 1024
    MiB = 1024*KiB
    GiB = 1024*MiB
    TiB = 1024*GiB
    if size >= TiB:
        return f"{size / TiB:.2f} TiB"
    if size >= GiB:
        return f"{size / GiB:.2f} GiB"
    if size >= MiB:
        return f"{size / MiB:.2f} MiB"
    if size >= KiB:
        return f"{size / KiB:.2f} KiB"
    return f"{size} B"

def clean_multipart_uploads(s3_bucket):
    cmd = ('aws', 's3api', 'list-multipart-uploads', '--bucket', s3_bucket)
    parts_json = subprocess.check_output(cmd)
    if len(parts_json) > 0:
        parts = json.loads(parts_json)
        for upload in parts['Uploads']:
            print('Cleaning remaining multipart', upload['Key'])
            cmd = ('aws', 's3api', 'abort-multipart-upload', '--bucket',
                   s3_bucket, '--key', upload['Key'], '--upload-id',
                   upload['UploadId'])
            subprocess.run(cmd, check=True)
