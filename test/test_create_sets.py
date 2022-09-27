#!/usr/bin/env python

from impl.create_sets import crawl, SetWriter
from impl.tools import BackupException
from impl.upload_sets import build_archive, get_list_files

import os
import pathlib
import pickle
import pytest
import random
import shutil
import string
import subprocess
import sys
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
REPRO_PATH = os.path.join(SCRIPT_PATH, 'state', 'repro.pickle.')
os.makedirs(os.path.dirname(REPRO_PATH), exist_ok=True)

def create_files(items):
    for item_path, size in items:
        if item_path.endswith('/'):
            os.makedirs(os.path.join(POOL_PATH, item_path), exist_ok=True)
        else:
            path, _ = os.path.split(item_path)
            os.makedirs(os.path.join(POOL_PATH, path), exist_ok=True)
            with open(os.path.join(POOL_PATH, item_path), 'wt') as f:
                f.write('x' * size)

def run_test_for_snapshot_paths(snapshot_path, pool_files, backup_paths, # pylint: disable=too-many-statements
                                upload_limit, num_expected_sets):
    snapshot_path = os.path.normpath(snapshot_path)
    backup_paths = tuple(map(os.path.normpath, backup_paths))

    for item in os.listdir(WORK_PATH):
        shutil.rmtree(os.path.join(WORK_PATH, item), ignore_errors=True)
    os.makedirs(POOL_PATH)
    os.makedirs(SET_PATH)
    create_files(pool_files)

    subprocess.run(('sudo', 'rm', '-f', snapshot_path), check=True)
    subprocess.run(('sudo', 'ln', '-s', POOL_PATH, snapshot_path), check=True)
    set_writer = SetWriter(snapshot_path, SET_PATH, ZFS_POOL)
    root_node = crawl(snapshot_path, backup_paths, upload_limit)
    root_node.create_backup_sets(set_writer, backup_paths)

    list_files = get_list_files(SET_PATH)
    if len(list_files) == 0:
        if len(backup_paths) == 0:
            # Nothing else to verify, backup set is empty
            return
        raise Exception('backup_paths not empty, but no set files found')

    if num_expected_sets is not None and len(list_files) != num_expected_sets:
        raise Exception(f"Wrong number of sets={len(list_files)}, expected={num_expected_sets}")

    archive_paths = []
    for list_file in list_files:
        _, archive_path = build_archive(list_file, WORK_PATH)
        archive_paths.append(archive_path)

    extract_path = os.path.join(WORK_PATH, 'extract')
    os.makedirs(extract_path, exist_ok=True)

    for archive_path in archive_paths:
        print(f"Extracting {archive_path}")
        cmd = (os.path.join(SCRIPT_PATH, '..', 'extract_archive'), archive_path, extract_path)
        subprocess.run(cmd, check=True)

    relative_snapshot_path = pathlib.Path(snapshot_path).relative_to('/')
    extract_base_path = os.path.join(extract_path, relative_snapshot_path)

    # Condition 1: All backup paths must be identical
    extract_backup_paths = set()
    for backup_path in backup_paths:
        snapshot_backup_path = os.path.join(snapshot_path, backup_path)
        extract_backup_path = os.path.join(extract_base_path, backup_path)
        cmd = ('diff', '-r', snapshot_backup_path, extract_backup_path)
        print(' '.join(cmd))
        subprocess.run(cmd, check=True)
        extract_backup_paths.add(extract_backup_path)

    for extract_backup_path in extract_backup_paths:
        if not os.path.exists(extract_backup_path):
            continue
        if os.path.islink(extract_backup_path) or os.path.isfile(extract_backup_path):
            os.remove(extract_backup_path)
        else:
            shutil.rmtree(extract_backup_path)

    # Condition 2: No extra files
    extra_files = []
    extra_dirs = []

    def raise_error(error):
        raise error

    for root, dirs, files in os.walk(extract_base_path, topdown=False,
                                     onerror=raise_error, followlinks=False):
        for file_ in files:
            extra_files.append(os.path.relpath(os.path.join(root, file_), extract_base_path))
        for dir_ in dirs:
            rel_path = os.path.relpath(os.path.join(root, dir_), extract_base_path)
            is_parent_dir = False
            for backup_path in backup_paths:
                if backup_path.startswith(os.path.join(rel_path, '')):
                    is_parent_dir = True
                    break
            if not is_parent_dir:
                extra_dirs.append(rel_path)

    if len(extra_files) > 0 or len(extra_dirs) > 0:
        raise Exception(f"Extract dir {extract_base_path} has extraneous items:"
                        f" files={extra_files}, dirs={extra_dirs}")

def run_test(pool_files, backup_paths, upload_limit, num_expected_sets=None, is_fuzz_run=False):
    for snapshot_path in SNAPSHOT_PATHS:
        try:
            run_test_for_snapshot_paths(snapshot_path, pool_files, backup_paths,
                                        upload_limit, num_expected_sets)
        except:
            if is_fuzz_run:
                i = 0
                while 1:
                    repro_path = f"{REPRO_PATH}{i}"
                    i += 1
                    if not os.path.exists(repro_path):
                        break
                with open(repro_path, 'wb') as f:
                    pickle.dump((snapshot_path, pool_files, backup_paths, upload_limit), f)
                print(f"Wrote reproducer to '{repro_path}', run"
                      f" '{__file__} --repro {repro_path}' to reproduce")
            raise

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

    run_test(pool_files, ('a', ), SIZE_SMALL, num_expected_sets=1)

def test_one_set_multiple_files():
    pool_files = (
        ('a/1', SIZE_SMALL),
        ('a/2', SIZE_SMALL),
        ('a/b/3', SIZE_SMALL)
    )

    run_test(pool_files, ('a', ), SIZE_SMALL * 3, num_expected_sets=1)

def test_three_dirs_include_two():
    pool_files = (
        ('a/1', SIZE_SMALL),
        ('b/2', SIZE_SMALL),
        ('c/3', SIZE_SMALL),
    )

    run_test(pool_files, ('a', 'c'), SIZE_SMALL * 3, num_expected_sets=1)

def test_file_and_subdir():
    pool_files = (
        ('a/1', SIZE_SMALL),
        ('a/b/2', SIZE_SMALL),
    )

    run_test(pool_files, ('a', ), SIZE_SMALL, num_expected_sets=2)

def test_top_level_files_one_set():
    pool_files = (
        ('1', SIZE_SMALL),
        ('2', SIZE_SMALL),
    )

    run_test(pool_files, ('1', '2'), SIZE_SMALL*2, num_expected_sets=1)

def test_top_level_files_two_sets():
    pool_files = (
        ('1', SIZE_SMALL),
        ('2', SIZE_SMALL),
    )

    run_test(pool_files, ('1', '2'), SIZE_SMALL, num_expected_sets=2)

def test_top_level_files_and_dirs():
    pool_files = (
        ('1', SIZE_SMALL),
        ('2', SIZE_SMALL),
        ('a/3', SIZE_SMALL),
        ('a/b/4', SIZE_SMALL),
        ('c/5', SIZE_SMALL),
        ('d/e/f/g/6', SIZE_SMALL),
    )

    run_test(pool_files, ('1', '2', 'a', 'c', 'd'), SIZE_SMALL * 2, num_expected_sets=3)
    run_test(pool_files, ('1', '2', 'a', 'c', 'd'), SIZE_SMALL, num_expected_sets=6)
    run_test(pool_files, ('1', 'a', 'c', 'd'), SIZE_SMALL * 2, num_expected_sets=3)
    run_test(pool_files, ('1', 'a', 'd'), SIZE_SMALL * 2, num_expected_sets=2)
    run_test(pool_files, ('2', 'a', 'd'), SIZE_SMALL * 2, num_expected_sets=2)
    run_test(pool_files, ('1', 'a', 'c'), SIZE_SMALL * 2, num_expected_sets=2)

def test_file_size_exceeds_upload_limit_throws():
    pool_files = (
        ('1', SIZE_SMALL),
    )

    try:
        run_test(pool_files, ('1',), SIZE_SMALL - 1)
        raise Exception('Expected exception, got none')
    except BackupException:
        pass

def test_path_fits_but_only_one_file_included():
    pool_files = (
        ('a/b/1', SIZE_SMALL),
        ('a/b/2', SIZE_SMALL),
    )

    run_test(pool_files, ('a/b/1',), SIZE_SMALL * 2, num_expected_sets=1)

def test_path_fits_but_only_one_subdir_included():
    pool_files = (
        ('a/b/1', SIZE_SMALL),
        ('a/c/2', SIZE_SMALL),
    )

    run_test(pool_files, ('a/b',), SIZE_SMALL * 2, num_expected_sets=1)

def test_empty_dir():
    pool_files = (
        ('a/', 0),
    )

    run_test(pool_files, ('a',), SIZE_SMALL, num_expected_sets=1)

def test_dir_specified_multiple_times():
    pool_files = (
        ('a/1', SIZE_SMALL),
    )

    run_test(pool_files, ('a', 'a'), SIZE_SMALL, num_expected_sets=1)

def test_file_specified_multiple_times():
    pool_files = (
        ('1', SIZE_SMALL),
    )

    run_test(pool_files, ('1', '1'), SIZE_SMALL, num_expected_sets=1)

def test_dir_trailing_slash():
    pool_files = (
        ('a/', 0),
    )

    run_test(pool_files, ('a/',), SIZE_SMALL, num_expected_sets=1)

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

    run_test(pool_files, backup_paths, upload_limit, is_fuzz_run=True)

def do_test_fuzz_n(num_runs):
    t1 = time.time()
    for _ in range(num_runs):
        do_test_fuzz()
    print(f"{num_runs} runs took {time.time()-t1} sec")

    return num_runs

def test_fuzz_quick():
    return do_test_fuzz_n(2)

@pytest.mark.longrunner
def test_fuzz_long():
    return do_test_fuzz_n(100)

if __name__ == '__main__':
    if '--repro' in sys.argv:
        repro_path = sys.argv[sys.argv.index('--repro') + 1]
        with open(repro_path, 'rb') as f:
            args = pickle.load(f)
            run_test_for_snapshot_paths(*args, num_expected_sets=None)
    elif '--fuzz' in sys.argv:
        # Endless fuzzing
        total_runs = 0
        try:
            while 1:
                total_runs += test_fuzz_long()
        finally:
            print(f"Runs: {total_runs}")
    else:
        print('Valid args: --repro, --fuzz')

subprocess.run(('sudo', 'umount', WORK_PATH), check=True)
