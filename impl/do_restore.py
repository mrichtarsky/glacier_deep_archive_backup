import json
import os
from queue import Queue
import subprocess
from threading import Thread
import time

# Number of days the object stays available for download after restore.
# If there is lots of data to download, the default may have to be increased.
RESTORATION_PERIOD_DAYS = 3

def get_files(s3_bucket, timestamp):
    cmd = ('aws', 's3api', 'list-objects-v2', '--bucket', s3_bucket, '--prefix', timestamp,
           '--query', "Contents[?StorageClass=='DEEP_ARCHIVE'].[Key, Size]",
           '--no-paginate', '--output', 'json')
    cp = subprocess.run(cmd, capture_output=True, check=True)
    cp.check_returncode()
    file_list = json.loads(cp.stdout.decode())

    return file_list

def request_restore(s3_bucket, file_, days, restore_tier, files_to_restore):
    restore_request = """{ "Days": %d, "GlacierJobParameters": { "Tier": "%s" } }""" \
                      % (days, restore_tier) # pylint: disable=consider-using-f-string
    print(f"Requesting restore for '{file_}', tier '{restore_tier}'")
    cmd = ('aws', 's3api', 'restore-object', '--bucket', s3_bucket, '--key', file_,
           '--restore-request', restore_request)
    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except subprocess.CalledProcessError as e:
        if not 'RestoreAlreadyInProgress' in e.stderr.decode():
            raise
    files_to_restore.append(file_)

# Thread 1
def wait_for_restore(s3_bucket, files_to_restore):
    while len(files_to_restore) > 0:
        restored_files = []
        for file_ in files_to_restore:
            cmd = ('aws', 's3api', 'head-object', '--bucket', s3_bucket, '--key', file_)
            cp = subprocess.run(cmd, capture_output=True, check=True)
            cp.check_returncode()
            status = json.loads(cp.stdout.decode())
            restore_status = status['Restore']
            if 'ongoing-request="false"' in restore_status:
                restored_files.append(file_)
        for restored_file in restored_files:
            files_to_restore.remove(restored_file)
            download_queue.put(restored_file)

download_in_progress = False

# Thread 2
def download_and_extract(s3_bucket, num_total_files, buffer_path, extract_path):
    global download_in_progress # pylint: disable=global-statement
    num_processed_files = 0
    while not download_queue.empty() or len(files_to_restore) > 0:
        archive_path = download_queue.get()
        download_in_progress = True
        bucket_path = f"s3://{s3_bucket}/{archive_path}"
        cmd = ('aws', 's3', 'cp', bucket_path, buffer_path)
        download_success = False
        for i in range(3):
            print (f"{num_processed_files+1}/{num_total_files}:"
                   f" Downloading {archive_path}, attempt {i+1}")
            try:
                subprocess.run(cmd, check=True)
                download_success = True
                break
            except subprocess.CalledProcessError as e:
                print('Error during download: ', e)
        if not download_success:
            raise Exception('Download failed, see above. Exiting.')
        archive_name = os.path.basename(archive_path)
        cmd = ('./extract_archive', os.path.join(buffer_path, archive_name), extract_path)
        subprocess.run(cmd, check=True)
        download_in_progress = False
        num_processed_files += 1

s3_bucket = os.environ['S3_BUCKET']
timestamp = os.environ['TIMESTAMP']
restore_tier = os.environ['RESTORE_TIER']
buffer_path = os.environ['BUFFER_PATH']
extract_path = os.environ['EXTRACT_PATH']

os.makedirs(extract_path, exist_ok=True)

files_to_restore = []
download_queue = Queue()

files = get_files(s3_bucket, timestamp)
for file_ in files:
    request_restore(s3_bucket, file_[0], RESTORATION_PERIOD_DAYS, restore_tier, files_to_restore)
num_total_files = len(files_to_restore)

wait_for_restore_thread = Thread(target=wait_for_restore,
                                 args=(s3_bucket, files_to_restore))
wait_for_restore_thread.daemon = True
wait_for_restore_thread.start()

download_and_extract_thread = Thread(target=download_and_extract,
                                     args=(s3_bucket, num_total_files, buffer_path, extract_path))
download_and_extract_thread.daemon = True
download_and_extract_thread.start()

prev_num_restores = None
prev_num_downloads = None

while 1:
    num_restores = len(files_to_restore)
    num_downloads = download_queue.qsize()
    if num_restores != prev_num_restores or num_downloads != prev_num_downloads:
        print(f"Remaining jobs: restores={num_restores}, downloads={num_downloads}")
        if not download_in_progress and num_restores > 0:
            print("(No further output while restores are pending, please be patient)")
        prev_num_restores = num_restores
        prev_num_downloads = num_downloads
    if num_restores + num_downloads == 0:
        wait_for_restore_thread.join()
        download_and_extract_thread.join()
        break
    time.sleep(5)
print('OK')
