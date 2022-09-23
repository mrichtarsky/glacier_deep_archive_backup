#!/usr/bin/env python

from impl.create_sets import crawl, SetWriter
from impl.tools import BackupException
from impl.upload_sets import build_archive, get_set_files

import os
import random
import shutil
import string
import subprocess
import time

SCRIPT_PATH = os.path.dirname(os.path.abspath(__file__))
WORK_PATH = os.path.join(SCRIPT_PATH, 'work')
os.makedirs(WORK_PATH, exist_ok=True)
subprocess.run(('sudo', 'mount', '-t', 'tmpfs', '-o', 'size=1024m',
                'glacier_deep_archive_backup_test', WORK_PATH), check=True)

POOL_PATH = os.path.join(WORK_PATH, 'pool')
ZFS_POOL = 'tank'
SNAPSHOT_PATHS = (
    '/glacier_deep_archive_backup_test',
    '/mnt/glacier_deep_archive_backup_test')
SET_PATH = os.path.join(WORK_PATH, 'sets')

def create_files(items):
    for item_path, size in items:
        if item_path.endswith('/'):
            os.makedirs(os.path.join(POOL_PATH, item_path), exist_ok=True)
        else:
            path, _ = os.path.split(item_path)
            os.makedirs(os.path.join(POOL_PATH, path), exist_ok=True)
            with open(os.path.join(POOL_PATH, item_path), 'wt') as f:
                f.write('x' * size)

def run_test_for_snapshot_paths(snapshot_path, pool_files, backup_paths, upload_limit):
    for item in os.listdir(WORK_PATH):
        shutil.rmtree(os.path.join(WORK_PATH, item), ignore_errors=True)
    os.makedirs(POOL_PATH)
    os.makedirs(SET_PATH)
    create_files(pool_files)

    subprocess.run(('sudo', 'rm', '-f', snapshot_path), check=True)
    subprocess.run(('sudo', 'ln', '-s', POOL_PATH, snapshot_path), check=True)
    set_writer = SetWriter(snapshot_path, SET_PATH, ZFS_POOL)
    root_node = crawl(snapshot_path, backup_paths, upload_limit)
    root_node.create_backup_sets(set_writer)

    set_files = get_set_files(SET_PATH)
    archive_paths = []
    for set_file in set_files:
        _, archive_path = build_archive(set_file, WORK_PATH)
        archive_paths.append(archive_path)

    extract_path = os.path.join(WORK_PATH, 'extract')
    os.makedirs(extract_path, exist_ok=True)
    for archive_path in archive_paths:
        cmd = (os.path.join(SCRIPT_PATH, '..', 'extract_archive'), archive_path, extract_path)
        subprocess.run(cmd, check=True)

    cmd = ('diff', '-r', snapshot_path, os.path.join(extract_path, snapshot_path))
    subprocess.run(cmd, check=True)

def run_test(pool_files, backup_paths, upload_limit):
    for snapshot_path in SNAPSHOT_PATHS:
        run_test_for_snapshot_paths(snapshot_path, pool_files, backup_paths, upload_limit)

# Letters: directories, numbers: files
# Trailing slash means directory
SIZE_SMALL = 10

def test_empty():
    pool_files = ()

    run_test(pool_files, (), SIZE_SMALL)

def test_one_set_single_file():
    pool_files = (
        ('a/1', SIZE_SMALL),
    )

    run_test(pool_files, ('a', ), SIZE_SMALL)

def test_one_set_multiple_files():
    pool_files = (
        ('a/1', SIZE_SMALL),
        ('a/2', SIZE_SMALL),
        ('a/b/3', SIZE_SMALL)
    )

    run_test(pool_files, ('a', ), SIZE_SMALL * 3)

def test_three_dirs_include_two():
    pool_files = (
        ('a/1', SIZE_SMALL),
        ('b/2', SIZE_SMALL),
        ('c/3', SIZE_SMALL),
    )

    run_test(pool_files, ('a', 'c'), SIZE_SMALL * 3)

def test_file_and_subdir():
    pool_files = (
        ('a/1', SIZE_SMALL),
        ('a/b/2', SIZE_SMALL),
    )

    run_test(pool_files, ('a', ), SIZE_SMALL)

def test_top_level_files_one_set():
    pool_files = (
        ('1', SIZE_SMALL),
        ('2', SIZE_SMALL),
    )

    run_test(pool_files, ('1', '2'), SIZE_SMALL*2)

def test_top_level_files_two_sets():
    pool_files = (
        ('1', SIZE_SMALL),
        ('2', SIZE_SMALL),
    )

    run_test(pool_files, ('1', '2'), SIZE_SMALL)

def test_top_level_files_and_dirs():
    pool_files = (
        ('1', SIZE_SMALL),
        ('2', SIZE_SMALL),
        ('a/3', SIZE_SMALL),
        ('a/b/4', SIZE_SMALL),
        ('c/5', SIZE_SMALL),
        ('d/e/f/g/6', SIZE_SMALL),
    )

    run_test(pool_files, ('1', '2', 'a', 'c', 'd'), SIZE_SMALL * 2)
    run_test(pool_files, ('1', '2', 'a', 'c', 'd'), SIZE_SMALL)
    run_test(pool_files, ('1', 'a', 'c', 'd'), SIZE_SMALL * 2)
    run_test(pool_files, ('1', 'a', 'd'), SIZE_SMALL * 2)
    run_test(pool_files, ('2', 'a', 'd'), SIZE_SMALL * 2)
    run_test(pool_files, ('1', 'a', 'c'), SIZE_SMALL * 2)

def test_file_size_exceeds_upload_limit_throws():
    pool_files = (
        ('1', SIZE_SMALL),
    )

    try:
        run_test(pool_files, ('1',), SIZE_SMALL - 1)
        raise Exception('Expected exception, got none')
    except BackupException:
        pass


def do_test_fuzz():
    MAX_FILES = 1000
    MAX_FILE_LENGTH = 40
    MAX_FILE_SIZE = 1000
    FILE_CHARS = string.ascii_lowercase + string.ascii_uppercase + string.digits

    num_files = random.randint(1, MAX_FILES)
    num_dirs = random.randint(0, MAX_FILES // 3)
    dirs = []
    for _ in range(num_dirs):
        base_dir = ''
        if random.randint(0, 3) == 0 and len(dirs) > 0:
            base_dir = random.choice(dirs)
        dir_name = ''.join(random.choices(FILE_CHARS, k=random.randint(1, MAX_FILE_LENGTH)))
        dirs.append(os.path.join(base_dir, dir_name) + '/')

    dirs_set = set(dirs)
    # Explicitly create all dirs, so we have empty ones
    pool_files = [ (dir_, 0) for dir_ in dirs]
    for _ in range(num_files):
        if len(dirs) > 0:
            dir_path = random.choice(dirs)
        else:
            dir_path = ''
        while 1:
            file_name = ''.join(random.choices(FILE_CHARS, k=random.randint(1, MAX_FILE_LENGTH)))
            file_path = os.path.join(dir_path, file_name)
            if file_path + '/' not in dirs_set:
                break
        file_size = random.randint(0, MAX_FILE_SIZE)
        pool_files.append((file_path, file_size))

    backup_paths = [ item[0] for item in \
        random.choices(pool_files, k=random.randint(0, len(pool_files))) ]

    upload_limit = random.randint(MAX_FILE_SIZE, 3 * MAX_FILE_SIZE)

    try:
        run_test(pool_files, backup_paths, upload_limit)
    except:
        print(f"test_fuzz failed, inputs: pool_files={pool_files}, "
              f"backup_paths={backup_paths}, upload_limit={upload_limit}")
        raise

def test_fuzz():
    NUM_RUNS = 100

    t1 = time.time()
    for _ in range(NUM_RUNS):
        do_test_fuzz()
    print(f"{NUM_RUNS} runs took {time.time()-t1} sec")

    return NUM_RUNS

if __name__ == '__main__':
    # Endless fuzzing
    total_runs = 0
    try:
        while 1:
            total_runs += test_fuzz()
    finally:
        print(f"Runs: {total_runs}")

subprocess.run(('sudo', 'umount', WORK_PATH), check=True)
