import json
import subprocess

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
