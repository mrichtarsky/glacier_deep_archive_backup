#!/usr/bin/env python

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

from impl.tools import (BackupException, SealAction, clean_multipart_uploads,
                        make_set_info_filename, normalize_bucket_dir, size_to_string,
                        size_to_string_factor, size_to_unit)

NUM_UPLOAD_RETRIES = 3


def get_list_files(set_path):
    list_files = []

    def raise_error(error):
        raise error

    for root, _, files in os.walk(set_path, topdown=False, onerror=raise_error,
                                  followlinks=False):
        for file_ in files:
            if os.path.splitext(file_)[1] == '.list':
                list_file = os.path.join(root, file_)
                list_files.append(list_file)

    list_files.sort()

    return list_files


def build_archive(snapshot_path, list_file, buffer_path, tar_extra_args=None):
    stem = os.path.splitext(os.path.basename(list_file))[0]
    archive_name = f'{stem}.tar.zstd.gpg'
    buffer_file = os.path.join(buffer_path, archive_name)
    cmd = ['impl/build_archive.sh', snapshot_path, list_file, buffer_file]
    if tar_extra_args:
        cmd.extend(tar_extra_args)
    print(f"Running '{' '.join(cmd)}'")
    subprocess.run(cmd, check=True)

    return archive_name, buffer_file


def get_set_info_for(list_file):
    info_file = make_set_info_filename(list_file)
    with open(info_file, 'rt') as info_file:
        info = json.load(info_file)
        return info


def archiver(archive_queue, snapshot_path, list_files, buffer_path, tar_extra_args):
    archive_file = None
    list_list_filepath = None
    contents_archive_file = None
    try:
        for index, list_file in enumerate(list_files, 1):
            while archive_queue.full():
                time.sleep(5)

            print(f"Set {index}/{len(list_files)}: Packing from list '{list_file}'")

            t0 = time.time()
            archive_name, archive_file = build_archive(snapshot_path, list_file,
                                                       buffer_path, tar_extra_args)
            archive_time_sec = time.time() - t0
            archive_size_bytes = os.path.getsize(archive_file)

            info = get_set_info_for(list_file)
            archived_bytes = info['size_bytes']

            stem = os.path.basename(list_file)
            list_list_filename = f'{stem}_contents.txt'
            list_list_filepath = os.path.join(buffer_path, list_list_filename)
            with open(list_list_filepath, 'wt') as f:
                print(list_file, file=f)
            contents_archive_name, contents_archive_file = build_archive('.',
                                                                        list_list_filepath,
                                                                        buffer_path)

            archive_queue.put((list_file, archive_name, archive_file, archive_time_sec,
                               archive_size_bytes, archived_bytes, list_list_filepath,
                               contents_archive_name, contents_archive_file))
            archive_file = None
            list_list_filepath = None
            contents_archive_file = None

        archive_queue.put(True)  # All processed, success
    except:  # pylint: disable=bare-except
        if archive_file is not None:
            os.unlink(archive_file)
        if list_list_filepath is not None:
            os.unlink(list_list_filepath)
        if contents_archive_file is not None:
            os.unlink(contents_archive_file)
        archive_queue.put(False)  # Failure


class Uploader:
    def __init__(self, s3_bucket, bucket_dir, timestamp):
        self.s3_bucket = s3_bucket
        self.bucket_path_prefix = f's3://{s3_bucket}/{bucket_dir}{timestamp}'

    def __enter__(self):
        return self

    def __exit__(self, type_, value_, traceback_):
        # During upload, files will be temporarily stored in S3 standard storage.
        # Failed uploads leave orphans behind, which will cause quite high costs.
        # So drop them here.
        clean_multipart_uploads(self.s3_bucket)

    @staticmethod
    def _is_internet_reachable():
        command = ('aws', 'sts', 'get-caller-identity')
        cp = subprocess.run(command, check=False, capture_output=True, text=True)
        output = (cp.stdout + cp.stderr).lower()
        if cp.returncode == 0 or 'could not connect' not in output:
            return True
        return False

    @staticmethod
    def _wait_for_internet():
        while not Uploader._is_internet_reachable():
            print('Internet connection to AWS does not work, waiting...')
            time.sleep(5)

    def upload(self, file_, archive_name, deep_archive):
        bucket_path = f'{self.bucket_path_prefix}/{archive_name}'
        cmd = ['aws', 's3', 'cp', file_, bucket_path]
        if deep_archive:
            cmd.extend(['--storage-class', 'DEEP_ARCHIVE'])
        print(f"Running '{' '.join(cmd)}'")
        t0 = time.time()
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            Uploader._wait_for_internet()
            raise
        return time.time() - t0


def package_and_upload(snapshot_path, set_path, buffer_path, uploader, tar_extra_args):  # pylint: disable=too-many-statements
    num_errors = 0
    list_files = get_list_files(set_path)

    total_size_bytes = 0
    for list_file in list_files:
        info = get_set_info_for(list_file)
        total_size_bytes += info['size_bytes']

    archived_bytes = 0  # uncompressed
    archive_size_bytes = 0  # compressed
    gross_uploaded_bytes = 0  # uncompressed
    net_uploaded_bytes = 0
    archive_time_sec = 0
    upload_time_sec = 0
    start_time_sec = time.time()

    def seconds_to_days(seconds):
        days, remainder = divmod(seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, _ = divmod(remainder, 60)
        comps = []
        if int(days) > 0:
            comps.append(f'{int(days)}d')
        if int(hours) > 0:
            comps.append(f'{int(hours)}h')
        comps.append(f'{int(minutes)}m')
        return ' '.join(comps)

    def print_status():
        elapsed_time_sec = time.time() - start_time_sec
        active_str = seconds_to_days(elapsed_time_sec)

        factor, unit = size_to_unit(total_size_bytes)
        archived_str = (f'{size_to_string_factor(archived_bytes, factor, None)}'
                        f'/{size_to_string_factor(total_size_bytes, factor, unit)}')
        try:
            archived_perc = 100 * archived_bytes / total_size_bytes
        except ZeroDivisionError:
            archived_perc = 100
        if archive_time_sec > 0:
            archived_per_sec_str = f'{size_to_string(archived_bytes / archive_time_sec)}'
        else:
            archived_per_sec_str = '? MiB'
        uploaded_str = (f'{size_to_string_factor(gross_uploaded_bytes, factor, None)}'
                        f'/{size_to_string_factor(total_size_bytes, factor, unit)}')
        try:
            upload_perc = 100 * gross_uploaded_bytes / total_size_bytes
        except ZeroDivisionError:
            upload_perc = 100
        if upload_time_sec > 0:
            upload_per_sec_str = f'{size_to_string(net_uploaded_bytes / upload_time_sec)}'
        else:
            upload_per_sec_str = '? MiB'
        if archive_size_bytes > 0:
            ratio_str = f'{archived_bytes / archive_size_bytes:.1f}x'
        else:
            ratio_str = '?'
        if (archived_bytes > 0 and archive_time_sec > 0 and upload_time_sec > 0
                and gross_uploaded_bytes > 0 and net_uploaded_bytes > 0):
            archived_bytes_per_sec = archived_bytes / archive_time_sec
            eta_archiving_sec = ((total_size_bytes - archived_bytes)
                                 / archived_bytes_per_sec)
            gross_remaining_upload_bytes = total_size_bytes - gross_uploaded_bytes
            # Pessimistic: Remaining compression is 1x
            net_uploaded_bytes_per_sec = net_uploaded_bytes / upload_time_sec
            max_eta_upload_sec = gross_remaining_upload_bytes / net_uploaded_bytes_per_sec
            # Optimistic: Compression ratio is constant as for data before
            min_eta_upload_sec = (max_eta_upload_sec *
                                  (net_uploaded_bytes / gross_uploaded_bytes))
            min_eta_str = seconds_to_days(eta_archiving_sec + min_eta_upload_sec)
            max_eta_str = seconds_to_days(eta_archiving_sec + max_eta_upload_sec)
        else:
            min_eta_str = '?'
            max_eta_str = '?'
        if min_eta_str == max_eta_str:
            eta_str = min_eta_str
        else:
            eta_str = f'{min_eta_str} - {max_eta_str}'

        msg = (f'Elapsed: {active_str}, Archived: {archived_str}'
               f' ({archived_perc:.1f}%, {archived_per_sec_str}/s)'
               f', Uploaded: {uploaded_str} ({upload_perc:.1f}%'
               f', {upload_per_sec_str}/s), Ratio: {ratio_str}, ETA: {eta_str}')

        print(msg)

    # Upload will usually be slower than archive building. So build the archives in the
    # background, so that we will always have an archive ready for upload.
    # At most two archives will exist in parallel (one of it in process of being uploaded).
    archive_queue = queue.Queue(maxsize=1)
    archive_thread = threading.Thread(target=archiver,
                                      args=(archive_queue, snapshot_path, list_files,
                                            buffer_path, tar_extra_args))

    archive_thread.daemon = True
    archive_thread.start()

    archive_index = 0

    while True:
        result = archive_queue.get()
        if result in (True, False):
            archive_thread.join()
            if result is False:
                num_errors += 1
            break
        archive_index += 1
        (list_file, archive_name, archive_file, archive_time_sec_job,
         archive_size_bytes_job, archived_bytes_job, list_list_filepath,
         contents_archive_name, contents_archive_file) = result
        upload_success = False

        try:
            archive_time_sec += archive_time_sec_job
            archive_size_bytes += archive_size_bytes_job
            archived_bytes += archived_bytes_job
            print_status()

            for i in range(NUM_UPLOAD_RETRIES):
                print(f'Set {archive_index}/{len(list_files)}: Uploading {archive_name}'
                      f', attempt {i+1}')

                try:
                    uploader.upload(contents_archive_file, contents_archive_name,
                                    deep_archive=False)
                    file_upload_time_sec = uploader.upload(archive_file, archive_name,
                                                           deep_archive=True)

                    upload_success = True
                    net_uploaded_bytes += os.path.getsize(archive_file)
                    gross_uploaded_bytes += archived_bytes_job
                    upload_time_sec += file_upload_time_sec
                    break
                except subprocess.CalledProcessError as e:
                    print(f'Error during upload: {e}')
                finally:
                    print_status()
        finally:
            # Delete archive in any case, retry will recreate it and we need the space
            os.unlink(archive_file)
            if upload_success:
                os.unlink(list_file)
                os.unlink(make_set_info_filename(list_file))

            # We will return, clean up
            exception_pending = sys.exc_info()[0] is not None
            if upload_success or exception_pending:
                os.unlink(list_list_filepath)
                os.unlink(contents_archive_file)

            if exception_pending:
                # Clean up files in queue. This is not totally clean, since the archiver thread
                # is still running and producing, so there can be leftovers.
                result = archive_queue.get(block=False)
                if result not in (True, False):
                    archive_file = result[2]
                    list_list_filepath = result[6]
                    contents_archive_file = result[8]
                    os.unlink(archive_file)
                    os.unlink(list_list_filepath)
                    os.unlink(contents_archive_file)

            if not upload_success:
                # When upload failed, backup_resume will have to be run.
                num_errors += 1

    return num_errors


def upload_restore_config(s3_bucket, bucket_dir, timestamp, settings, buffer_path,
                          uploader):
    buffer_path_base = os.path.dirname(buffer_path)
    impl_path = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(impl_path, 'restore.tmpl')) as f:
        template_str = f.read()
    config = template_str.format(s3_bucket=s3_bucket, bucket_dir=bucket_dir,
                                 timestamp=timestamp, buffer_path_base=buffer_path_base)

    settings_filename = os.path.basename(settings)
    stem, ext = os.path.splitext(settings_filename)
    if stem.startswith('backup'):
        stem = stem.replace('backup', 'restore')
    else:
        stem = f'restore_{stem}'
    stem += f'_{timestamp}'
    restore_filename = stem + ext
    restore_file = os.path.join(buffer_path, restore_filename)
    with open(restore_file, 'wt') as f:
        print(config, file=f)

    for i in range(NUM_UPLOAD_RETRIES):
        print(f'Uploading restore config {restore_filename}, attempt {i+1}')
        try:
            uploader.upload(restore_file, restore_filename, deep_archive=False)
            break
        except subprocess.CalledProcessError as e:
            print(f'Error during upload: {e}')
    else:
        os.unlink(restore_file)
        return 1
    os.unlink(restore_file)
    return 0


if __name__ == '__main__':
    snapshot_path = os.path.normpath(os.environ['SNAPSHOT_PATH'])
    set_path = os.environ['SET_PATH']
    buffer_path = os.environ['BUFFER_PATH']
    s3_bucket = os.environ['S3_BUCKET']
    bucket_dir = os.environ['BUCKET_DIR']
    bucket_dir = normalize_bucket_dir(bucket_dir)
    timestamp = os.environ['TIMESTAMP']
    settings = os.environ['SETTINGS']
    upload_limit = int(os.environ['UPLOAD_LIMIT_MB']) * 1024 * 1024
    seal_action = SealAction()
    if seal_action.is_skip_sealed():
        extra_args = ('--exclude=*/.GDAB_SEALED', '--exclude=*/.GDAB_SEALED/*')
    else:
        extra_args = ()
    _, _, bytes_free = shutil.disk_usage(buffer_path)
    if bytes_free < upload_limit:
        raise BackupException(f'Not enough disk space in buffer path {buffer_path} '
                              f'(upload_limit={size_to_string(upload_limit)}, '
                              f'bytes_free={size_to_string(bytes_free)})')

    with Uploader(s3_bucket, bucket_dir, timestamp) as uploader:
        num_errors = package_and_upload(snapshot_path, set_path, buffer_path, uploader,
                                        extra_args)

        num_errors += upload_restore_config(s3_bucket, bucket_dir.rstrip('/'),
                                            timestamp, settings, buffer_path, uploader)

    sys.exit(0 if num_errors == 0 else 1)
