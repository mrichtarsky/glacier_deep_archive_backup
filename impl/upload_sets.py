#!/usr/bin/env python

from impl.tools import clean_multipart_uploads

import os
import subprocess
import sys

def get_set_files(set_path):
    set_files = []

    def raise_error(error):
        raise error

    for root, _, files in os.walk(set_path, topdown=False, onerror=raise_error, followlinks=False):
        for file_ in files:
            set_file = os.path.join(root, file_)
            set_files.append(set_file)

    return set_files

def build_archive(set_file, buffer_path):
    archive_name = f"{os.path.basename(set_file)}.tar.zstd.ssl"
    buffer_file = os.path.join(buffer_path, archive_name)
    cmd = ('impl/build_archive.sh', set_file, buffer_file)
    print('Running', ' '.join(cmd))
    subprocess.run(cmd, check=True)

    return archive_name, buffer_file

def package_and_upload(set_path, buffer_path, s3_bucket, timestamp):
    num_errors = 0
    set_files = get_set_files(set_path)

    for index, set_file in enumerate(set_files, 1):
        print(f"{index}/{len(set_files)}: Packing set {set_file}")
        archive_name, archive_file = build_archive(set_file, buffer_path)

        stem = os.path.basename(set_file)
        set_set_file = os.path.join(buffer_path, f"{stem}_contents.txt")
        with open(set_set_file, 'wt') as f:
            print(set_file, file=f)
        contents_archive_name, contents_archive_file = build_archive(set_set_file, buffer_path)

        upload_success = False
        for i in range(3):
            print(f"{index}/{len(set_files)}: Uploading {archive_name}, attempt {i+1}")

            def do_upload(file_, archive_name, deep_archive):
                bucket_path = f"s3://{s3_bucket}/{timestamp}/{archive_name}"
                cmd = ['aws', 's3', 'cp', file_, bucket_path]
                if deep_archive:
                    cmd.extend(['--storage-class', 'DEEP_ARCHIVE'])
                print('Running', cmd)
                subprocess.run(cmd, check=True)

            try:
                do_upload(contents_archive_file, contents_archive_name, deep_archive=False)
                do_upload(archive_file, archive_name, deep_archive=True)

                upload_success = True
                break
            except subprocess.CalledProcessError as e:
                print('Error during upload: ', e)

        # Delete archive in any case, retry will recreate it and we need the space
        os.unlink(archive_file)
        if upload_success:
            os.unlink(set_file)
        else:
            num_errors += 1

    return num_errors

if __name__ == '__main__':
    set_path = os.environ['SET_PATH']
    buffer_path = os.environ['BUFFER_PATH']
    s3_bucket = os.environ['S3_BUCKET']
    timestamp = os.environ['TIMESTAMP']

    num_errors = package_and_upload(set_path, buffer_path, s3_bucket, timestamp)

    # During upload, files will be temporarily stored in S3 standard storage.
    # Failed uploads leave orphans behind, which will cause quite high costs.
    # So drop them here.
    clean_multipart_uploads(s3_bucket)
    sys.exit(0 if num_errors == 0 else 1)
