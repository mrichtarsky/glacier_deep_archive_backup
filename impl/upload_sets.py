#!/usr/bin/env python

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

from impl.tools import (BackupException, clean_multipart_uploads,
                        make_set_info_filename, size_to_string, size_to_string_factor,
                        size_to_unit)


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


def build_archive(snapshot_path, list_file, buffer_path):
    stem = os.path.splitext(os.path.basename(list_file))[0]
    archive_name = f'{stem}.tar.zstd.gpg'
    buffer_file = os.path.join(buffer_path, archive_name)
    cmd = ('impl/build_archive.sh', snapshot_path, list_file, buffer_file)
    print(f"Running '{' '.join(cmd)}'")
    subprocess.run(cmd, check=True)

    return archive_name, buffer_file


def get_set_info_for(list_file):
    info_file = make_set_info_filename(list_file)
    with open(info_file, 'rt') as info_file:
        info = json.load(info_file)
        return info


def archiver(archive_queue, snapshot_path, list_files, buffer_path):
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
                                                       buffer_path)
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


def package_and_upload(snapshot_path, set_path, buffer_path, s3_bucket, bucket_dir,
                       timestamp):  # pylint: disable=too-many-statements
    # Avoid extraneous directories on S3, normalize path
    bucket_dir = bucket_dir.strip('/')
    if len(bucket_dir):
        bucket_dir += '/'

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
        archived_perc = 100 * archived_bytes / total_size_bytes
        if archive_time_sec > 0:
            archived_per_sec_str = f'{size_to_string(archived_bytes / archive_time_sec)}'
        else:
            archived_per_sec_str = '? MiB'
        uploaded_str = (f'{size_to_string_factor(gross_uploaded_bytes, factor, None)}'
                        f'/{size_to_string_factor(total_size_bytes, factor, unit)}')
        upload_perc = 100 * gross_uploaded_bytes / total_size_bytes
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
                                      args=(archive_queue, snapshot_path, list_files, buffer_path))

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

            for i in range(3):
                print(f'Set {archive_index}/{len(list_files)}: Uploading {archive_name}'
                      f', attempt {i+1}')

                def do_upload(file_, archive_name, deep_archive):
                    bucket_path = f's3://{s3_bucket}/{bucket_dir}{timestamp}/{archive_name}'
                    cmd = ['aws', 's3', 'cp', file_, bucket_path]
                    if deep_archive:
                        cmd.extend(['--storage-class', 'DEEP_ARCHIVE'])
                    print(f"Running '{' '.join(cmd)}'")
                    t0 = time.time()
                    subprocess.run(cmd, check=True)
                    return time.time() - t0

                try:
                    do_upload(contents_archive_file, contents_archive_name,
                              deep_archive=False)
                    file_upload_time_sec = do_upload(archive_file, archive_name,
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


if __name__ == '__main__':
    snapshot_path = os.path.normpath(os.environ['SNAPSHOT_PATH'])
    set_path = os.environ['SET_PATH']
    buffer_path = os.environ['BUFFER_PATH']
    s3_bucket = os.environ['S3_BUCKET']
    bucket_dir = os.environ['BUCKET_DIR']
    timestamp = os.environ['TIMESTAMP']

    upload_limit = int(os.environ['UPLOAD_LIMIT_MB']) * 1024 * 1024
    _, _, bytes_free = shutil.disk_usage(buffer_path)

    if bytes_free < upload_limit:
        raise BackupException(f'Not enough disk space in buffer path {buffer_path} '
                              f'(upload_limit={size_to_string(upload_limit)}, '
                              f'bytes_free={size_to_string(bytes_free)})')

    num_errors = package_and_upload(snapshot_path, set_path, buffer_path, s3_bucket,
                                    bucket_dir, timestamp)

    # During upload, files will be temporarily stored in S3 standard storage.
    # Failed uploads leave orphans behind, which will cause quite high costs.
    # So drop them here.
    clean_multipart_uploads(s3_bucket)
    sys.exit(0 if num_errors == 0 else 1)
