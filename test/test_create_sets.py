#!/usr/bin/env python

import os
import pickle
import random
import shutil
import string
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from impl.create_sets import Path, SetWriter, crawl
from impl.tools import BackupException, SealAction, glob_backup_paths
from impl.upload_sets import build_archive, get_list_files

SCRIPT_PATH = os.path.dirname(os.path.abspath(__file__))
WORK_PATH = os.path.join(SCRIPT_PATH, 'work')
os.makedirs(WORK_PATH, exist_ok=True)
subprocess.run(('sudo', 'mount', '-t', 'tmpfs', '-o', 'size=1024m',
                'glacier_deep_archive_backup_test', WORK_PATH), check=True)

POOL_PATH = os.path.join(WORK_PATH, 'pool')
ZFS_POOL = 'tank'

# Test two different kinds of snapshot paths
SNAPSHOT_PATHS = ('/glacier_deep_archive_backup_test',
                  '/mnt/glacier_deep_archive_backup_test')
SET_PATH = os.path.join(WORK_PATH, 'sets')
REPRO_PATH = os.path.join(SCRIPT_PATH, 'state', 'repro.pickle')
os.makedirs(os.path.dirname(REPRO_PATH), exist_ok=True)

thread_pool = ThreadPoolExecutor(max_workers=2 * os.cpu_count())

class TestException(Exception):
    pass


def create_files(items):
    for item_path, size in items:
        if item_path.endswith('/'):
            os.makedirs(os.path.join(POOL_PATH, item_path), exist_ok=True)
        else:
            path, _ = os.path.split(item_path)
            os.makedirs(os.path.join(POOL_PATH, path), exist_ok=True)
            with open(os.path.join(POOL_PATH, item_path), 'wb') as f:
                f.write(random.randbytes(size))


class ArchiveBuilder:
    def __init__(self, snapshot_path, work_path):
        self.snapshot_path = snapshot_path
        self.work_path = work_path

    def run(self, list_file):
        _, buffer_file = build_archive(self.snapshot_path, list_file, self.work_path)
        return buffer_file


def run_cmd(cmd):
    subprocess.run(cmd, check=True)


# pylint: disable=too-many-statements
def run_test_for_snapshot_paths(snapshot_path, pool_files, backup_paths,
                                num_expected_warnings, num_expected_sets,
                                num_expected_files):
    snapshot_path = os.path.normpath(snapshot_path)
    backup_paths_unglobbed = tuple(map(os.path.normpath, backup_paths))

    print('Cleaning work dir')
    for item in os.listdir(WORK_PATH):
        shutil.rmtree(os.path.join(WORK_PATH, item), ignore_errors=True)
    print('Creating pool')
    os.makedirs(POOL_PATH)
    os.makedirs(SET_PATH)
    create_files(pool_files)

    subprocess.run(('sudo', 'rm', '-f', snapshot_path), check=True)
    subprocess.run(('sudo', 'ln', '-s', POOL_PATH, snapshot_path), check=True)

    backup_paths, num_warnings = glob_backup_paths(backup_paths_unglobbed,
                                                   snapshot_path)

    if num_expected_warnings is not None and num_expected_warnings != num_warnings:
        raise TestException(f'Mismatch: num_expected_warnings={num_expected_warnings}'
                            f', num_warnings={num_warnings}')

    set_writer = SetWriter(snapshot_path, SET_PATH, ZFS_POOL)
    root_node = crawl(snapshot_path, backup_paths, SealAction())
    root_node.create_backup_sets(set_writer, backup_paths)

    list_files = get_list_files(SET_PATH)
    if len(list_files) == 0:
        if len(backup_paths) == 0:
            # Nothing else to verify, backup set is empty
            return
        raise TestException('backup_paths not empty, but no set files found')

    if num_expected_sets is not None and len(list_files) != num_expected_sets:
        raise TestException(f'Wrong number of sets={len(list_files)}'
                            f', expected={num_expected_sets}')

    archive_builder = ArchiveBuilder(snapshot_path, WORK_PATH)
    archive_paths = list(thread_pool.map(archive_builder.run, list_files))

    extract_path = os.path.join(WORK_PATH, 'extract')
    os.makedirs(extract_path, exist_ok=True)

    cmds = []
    for archive_path in archive_paths:
        print(f'Extracting {archive_path}')
        extract_archive_path = os.path.join(SCRIPT_PATH, '..', 'extract_archive')
        cmd = (extract_archive_path, archive_path, extract_path)
        cmds.append(cmd)
    list(thread_pool.map(run_cmd, cmds))

    def raise_error(error):
        raise error

    # If specified, number of files must match
    if num_expected_files is not None:
        num_files = 0
        for _, _, files in os.walk(extract_path, topdown=False, onerror=raise_error,
                                   followlinks=False):
            num_files += len(files)
        if num_expected_files != num_files:
            msg = f'Mismatch: num_expected_files={num_expected_files}, num_files={num_files}'
            raise TestException(msg)

    # Condition 1: All backup paths must be identical
    extract_backup_paths = set()
    for backup_path in backup_paths:
        snapshot_backup_path = os.path.join(snapshot_path, backup_path)
        extract_backup_path = os.path.join(extract_path, backup_path)
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

    for root, dirs, files in os.walk(extract_path, topdown=False, onerror=raise_error,
                                     followlinks=False):
        for file_ in files:
            extra_files.append(os.path.relpath(os.path.join(root, file_), extract_path))
        for dir_ in dirs:
            rel_path = os.path.relpath(os.path.join(root, dir_), extract_path)
            is_parent_dir = False
            for backup_path in backup_paths:
                if backup_path.startswith(os.path.join(rel_path, '')):
                    is_parent_dir = True
                    break
            if not is_parent_dir:
                extra_dirs.append(rel_path)

    if len(extra_files) > 0 or len(extra_dirs) > 0:
        raise TestException(f'Extract dir {extract_path} has extraneous items:'
                            f' files={extra_files}, dirs={extra_dirs}')


def run_test(pool_files, backup_paths, upload_limit, num_expected_warnings=None,
             num_expected_sets=None, num_expected_files=None, is_fuzz_run=False):
    Path.UPLOAD_LIMIT = upload_limit
    for snapshot_path in SNAPSHOT_PATHS:
        try:
            run_test_for_snapshot_paths(snapshot_path, pool_files, backup_paths,
                                        num_expected_warnings, num_expected_sets,
                                        num_expected_files)
        except:
            if is_fuzz_run:
                i = 0
                while 1:
                    repro_path = f'{REPRO_PATH}{i}'
                    i += 1
                    if not os.path.exists(repro_path):
                        break
                with open(repro_path, 'wb') as f:
                    repro_info = (snapshot_path, pool_files, backup_paths, upload_limit)
                    pickle.dump(repro_info, f)
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
    pool_files = (('a/1', SIZE_SMALL),)

    run_test(pool_files, ('a',), SIZE_SMALL, num_expected_sets=1, num_expected_files=1)


def test_one_set_multiple_files():
    pool_files = (
        ('a/1', SIZE_SMALL),
        ('a/2', SIZE_SMALL),
        ('a/b/3', SIZE_SMALL),
    )

    run_test(pool_files, ('a',), SIZE_SMALL * 3, num_expected_sets=1,
             num_expected_files=3)


def test_three_dirs_include_two():
    pool_files = (
        ('a/1', SIZE_SMALL),
        ('b/2', SIZE_SMALL),
        ('c/3', SIZE_SMALL),
    )

    run_test(pool_files, ('a', 'c'), SIZE_SMALL * 3, num_expected_sets=1,
             num_expected_files=2)


def test_file_and_subdir():
    pool_files = (
        ('a/1', SIZE_SMALL),
        ('a/b/2', SIZE_SMALL),
    )

    run_test(pool_files, ('a',), SIZE_SMALL, num_expected_sets=2, num_expected_files=2)


def test_top_level_files_one_set():
    pool_files = (
        ('1', SIZE_SMALL),
        ('2', SIZE_SMALL),
    )

    run_test(pool_files, ('1', '2'), SIZE_SMALL * 2, num_expected_sets=1,
             num_expected_files=2)


def test_top_level_files_two_sets():
    pool_files = (
        ('1', SIZE_SMALL),
        ('2', SIZE_SMALL),
    )

    run_test(pool_files, ('1', '2'), SIZE_SMALL, num_expected_sets=2,
             num_expected_files=2)


def test_top_level_files_and_dirs():
    pool_files = (
        ('1', SIZE_SMALL),
        ('2', SIZE_SMALL),
        ('a/3', SIZE_SMALL),
        ('a/b/4', SIZE_SMALL),
        ('c/5', SIZE_SMALL),
        ('d/e/f/g/6', SIZE_SMALL),
    )

    run_test(pool_files, ('1', '2', 'a', 'c', 'd'), SIZE_SMALL * 2, num_expected_sets=3,
             num_expected_files=6)
    run_test(pool_files, ('1', '2', 'a', 'c', 'd'), SIZE_SMALL, num_expected_sets=6,
             num_expected_files=6)
    run_test(pool_files, ('1', 'a', 'c', 'd'), SIZE_SMALL * 2, num_expected_sets=3,
             num_expected_files=5)
    run_test(pool_files, ('1', 'a', 'd'), SIZE_SMALL * 2, num_expected_sets=2,
             num_expected_files=4)
    run_test(pool_files, ('2', 'a', 'd'), SIZE_SMALL * 2, num_expected_sets=2,
             num_expected_files=4)
    run_test(pool_files, ('1', 'a', 'c'), SIZE_SMALL * 2, num_expected_sets=2,
             num_expected_files=4)


def test_file_size_exceeds_upload_limit_throws():
    pool_files = (('1', SIZE_SMALL),)

    try:
        run_test(pool_files, ('1',), SIZE_SMALL - 1)
        raise TestException('Expected exception, got none')
    except BackupException:
        pass


def test_path_fits_but_only_one_file_included():
    pool_files = (
        ('a/b/1', SIZE_SMALL),
        ('a/b/2', SIZE_SMALL),
    )

    run_test(pool_files, ('a/b/1',), SIZE_SMALL * 2, num_expected_sets=1,
             num_expected_files=1)


def test_path_fits_but_only_one_subdir_included():
    pool_files = (
        ('a/b/1', SIZE_SMALL),
        ('a/c/2', SIZE_SMALL),
    )

    run_test(pool_files, ('a/b',), SIZE_SMALL * 2, num_expected_sets=1,
             num_expected_files=1)


def test_empty_dir():
    pool_files = (('a/', 0),)

    run_test(pool_files, ('a',), SIZE_SMALL, num_expected_sets=1, num_expected_files=0)


def test_dir_specified_multiple_times():
    pool_files = (('a/1', SIZE_SMALL),)

    run_test(pool_files, ('a', 'a'), SIZE_SMALL, num_expected_sets=1,
             num_expected_files=1)


def test_file_specified_multiple_times():
    pool_files = (('1', SIZE_SMALL),)

    run_test(pool_files, ('1', '1'), SIZE_SMALL, num_expected_sets=1,
             num_expected_files=1)


def test_dir_trailing_slash():
    pool_files = (('a/', 0),)

    run_test(pool_files, ('a/',), SIZE_SMALL, num_expected_sets=1, num_expected_files=0)


def test_glob_files_current_dir_star():
    pool_files = (
        ('12244.py', SIZE_SMALL),
        ('1.sh', SIZE_SMALL),
        ('____2.py', SIZE_SMALL),
    )

    run_test(pool_files, ('*.py',), SIZE_SMALL, num_expected_sets=2,
             num_expected_files=2)


def test_glob_files_current_dir_star_suffix():
    pool_files = (
        ('12244.py', SIZE_SMALL),
        ('1.sh', SIZE_SMALL),
        ('____2.py', SIZE_SMALL),
    )

    run_test(pool_files, ('1*',), SIZE_SMALL * 2, num_expected_sets=1,
             num_expected_files=2)


def test_glob_files_current_dir_question_mark():
    pool_files = (
        ('1.py', SIZE_SMALL),
        ('1.sh', SIZE_SMALL),
        ('2.py', SIZE_SMALL),
    )

    run_test(pool_files, ('?.py',), SIZE_SMALL, num_expected_sets=2,
             num_expected_files=2)


def test_glob_dirs_current_dir_star():
    pool_files = (
        ('332aa/1.py', SIZE_SMALL),
        ('tnscb/1.sh', SIZE_SMALL),
        ('_--aa/2.py', SIZE_SMALL),
    )

    run_test(pool_files, ('*a*',), SIZE_SMALL, num_expected_sets=2,
             num_expected_files=2)


def test_glob_dirs_current_dir_question_mark():
    pool_files = (
        ('aa/1.py', SIZE_SMALL),
        ('ab/1.sh', SIZE_SMALL),
        ('aa/2.py', SIZE_SMALL),
    )

    run_test(pool_files, ('?a',), SIZE_SMALL, num_expected_sets=2, num_expected_files=2)


def test_glob_files_subdir_star():
    pool_files = (
        ('a/1.py', SIZE_SMALL),
        ('a/1.sh', SIZE_SMALL),
        ('a/2.py', SIZE_SMALL),
    )

    run_test(pool_files, ('a/*.sh',), SIZE_SMALL, num_expected_sets=1,
             num_expected_files=1)


def test_glob_files_subdir_question_mark():
    pool_files = (
        ('a/1.py', SIZE_SMALL),
        ('a/1.sh', SIZE_SMALL),
        ('a/2.py', SIZE_SMALL),
    )

    run_test(pool_files, ('a/??sh',), SIZE_SMALL, num_expected_sets=1,
             num_expected_files=1)


def test_glob_dirs_subdir_star():
    pool_files = (
        ('a_long/b/1.py', SIZE_SMALL),
        ('a_long/b/2.py', SIZE_SMALL),
        ('a_long/cc/3.sh', SIZE_SMALL),
        ('d_long/e/4.py', SIZE_SMALL),
    )

    run_test(pool_files, ('a_long/*c/*sh',), SIZE_SMALL * 2, num_expected_sets=1,
             num_expected_files=1)


def test_glob_dirs_subdir_question_mark():
    pool_files = (
        ('a_long/b/1.py', SIZE_SMALL),
        ('a_long/b/2.py', SIZE_SMALL),
        ('a_long/b/3.sh', SIZE_SMALL),
        ('a_long/cc/4.sh', SIZE_SMALL),
        ('d_long/e/5.py', SIZE_SMALL),
    )

    run_test(pool_files, ('a_long/?/*py',), SIZE_SMALL * 2, num_expected_sets=1,
             num_expected_files=2)


def test_glob_dirs_recursive():
    pool_files = (
        ('a/1.py', SIZE_SMALL),
        ('b/1.sh', SIZE_SMALL),
        ('a/2.py', SIZE_SMALL),
        ('b/2.pyc', SIZE_SMALL),
        ('a/c/3.py', SIZE_SMALL),
        ('a/c/3.pyc', SIZE_SMALL),
        ('d/e/f/4.py', SIZE_SMALL),
        ('d/e/f/4.sh', SIZE_SMALL),
        ('5.py', SIZE_SMALL),
        ('5.pyc', SIZE_SMALL),
    )

    run_test(pool_files, ('**/*py',), SIZE_SMALL * 3, num_expected_sets=2,
             num_expected_files=5)
    run_test(pool_files, ('**/1.*',), SIZE_SMALL, num_expected_sets=2,
             num_expected_files=2)
    run_test(pool_files, ('**/?.???',), SIZE_SMALL * 2, num_expected_sets=2,
             num_expected_files=3)


def test_no_match():
    pool_files = (
        ('aa/1.py', SIZE_SMALL),
        ('ab/1.sh', SIZE_SMALL),
        ('aa/2.py', SIZE_SMALL),
    )

    run_test(pool_files, ('xyz',), SIZE_SMALL, num_expected_warnings=1,
             num_expected_files=0)
    run_test(pool_files, ('xyz', 'rs'), SIZE_SMALL, num_expected_warnings=2,
             num_expected_files=0)

    run_test(pool_files, ('aa/xyz',), SIZE_SMALL, num_expected_warnings=1,
             num_expected_files=0)

    # globs
    run_test(pool_files, ('xyz*',), SIZE_SMALL, num_expected_warnings=1,
             num_expected_files=0)
    run_test(pool_files, ('*xyz',), SIZE_SMALL, num_expected_warnings=1,
             num_expected_files=0)
    run_test(pool_files, ('aa/xyz',), SIZE_SMALL, num_expected_warnings=1,
             num_expected_files=0)


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
    pool_files = [(dir_, 0) for dir_ in dirs]
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
    print(f'{num_runs} runs took {time.time()-t1} sec')

    return num_runs


def test_fuzz_quick():
    do_test_fuzz_n(2)


@pytest.mark.longrunner
def test_fuzz_long():
    FUZZ_LONG_NUM_RUNS = 100
    do_test_fuzz_n(FUZZ_LONG_NUM_RUNS)


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
                test_fuzz_long()
                total_runs += test_fuzz_long.FUZZ_LONG_NUM_RUNS
        finally:
            print(f'Runs: {total_runs}')
    else:
        print('Valid args: --repro, --fuzz')

subprocess.run(('sudo', 'umount', WORK_PATH), check=True)
