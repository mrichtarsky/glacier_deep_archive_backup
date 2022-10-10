#!/usr/bin/env python

'''
Ran by do_backup_to_aws.sh

Parameters passed by environment, backup paths on command line.

Creates sets with UPLOAD_LIMIT maximum size,
stores the list of files in the set in distinct files in SET_PATH.
SNAPSHOT_PATH is where the ZFS snapshot is mounted.

Sets are processed later by upload_sets.py.
'''

from impl.tools import BackupException, make_info_filename, size_to_string

import binpacking
import copy
from functools import lru_cache
import json
import os
import pathlib
import pickle
import re
import stat
import subprocess
import sys

class SetWriter():
    def __init__(self, snapshot_path, set_path, zfs_pool):
        self.snapshot_path = snapshot_path
        self.set_path = set_path
        self.zfs_pool = zfs_pool

    def _make_archive_name(self, items):
        if len(items) == 1:
            prefix = items[0]
        else:
            items.sort()
            max_prefix_length = min(len(items[0]), len(items[-1]))

            i = 0
            while i < max_prefix_length and items[0][i] == items[-1][i]:
                i += 1

            prefix = items[0][0:i]

        name = prefix.replace(self.snapshot_path, self.zfs_pool)
        name = re.sub('[^a-zA-Z0-9_-]', '_', name)
        name = name.rstrip('_')
        return name

    def write_set(self, items, size, num_dirs, num_files):
        print(f"Set: {len(items)} path(s), {size_to_string(size)}"
              f", {num_dirs} dir(s), {num_files} file(s)")
        archive_name = self._make_archive_name(items)
        print(f"  Archive name: {archive_name}")

        counter = 0
        while True:
            list_filename = os.path.join(self.set_path,
                                        f"{archive_name}_{counter:03d}.list")
            if not os.path.exists(list_filename):
                break
            counter += 1

        with open(list_filename, 'wt') as list_file:
            for item in items:
                item = os.path.relpath(item, self.snapshot_path)
                print('  ', item)
                print(item, file=list_file)

        info_filename = make_info_filename(list_filename)
        with open(info_filename, 'wt') as info_file:
            info = { 'size_bytes': size }
            json.dump(info, info_file)

class Path():
    def __init__(self, name, parent, upload_limit):
        self.parent = parent
        self.name = name
        self.upload_limit = upload_limit
        self.files = set()
        self.dirs = {}
        self.size = None # Lazily computed

    def add_file(self, name, size):
        if name in ('.', '..', ''):
            raise BackupException(f"Invalid filename {name}")

        if size > self.upload_limit:
            raise BackupException(f"File size exceeds upload limit: {self.get_full_path()}/{name}"
                                  f" (size={size_to_string(size)}, "
                                  f"upload_limit={size_to_string(self.upload_limit)})")
        self.files.add((name, size))

    def get_dir(self, name):
        if name in ('.', '..', ''):
            raise BackupException(f"Invalid dirname {name}")

        return self.dirs.setdefault(name, Path(name, self, self.upload_limit))

    @lru_cache
    def get_snapshot_path(self):
        node = self
        while node.parent is not None:
            node = node.parent
        return node.name

    def get_full_path(self):
        if self.parent is None:
            return self.name
        return os.path.join(self.parent.get_full_path(), self.name)

    def get_size(self):
        if self.size is None:
            size = 0
            for file_ in self.files:
                size += file_[1]
            for dir_ in self.dirs.values():
                size += dir_.get_size()
            self.size = size
        return self.size

    def __str__(self):
        out = ''
        for dir_ in self.dirs.values():
            out += str(dir_)
        if len(self.files) > 0:
            path = self.get_full_path()
            out += '\n'.join(map(lambda file_: os.path.join(path, file_[0]), self.files)) + '\n'
        return out

    def _is_inside_backup_paths(self, backup_paths, path):
        snapshot_path = self.get_snapshot_path()
        rel_path = os.path.relpath(path, snapshot_path)
        for backup_path in backup_paths:
            exact_match = backup_path == rel_path # File or path
            path_prefix_match = rel_path.startswith(os.path.join(backup_path, ''))
            if exact_match or path_prefix_match:
                return True
        return False

    def get_node(self, path):
        if self.parent is None:
            path = os.path.relpath(path, self.name)
        node = self
        if len(path) > 0 and path != '.': # relpath returns '.' for top level dir
            comps = path.split('/')
            for comp in comps:
                node = node.get_dir(comp)
        return node

    def get_num_dirs_files(self):
        num_dirs = 1 # Including current dir
        num_files = len(self.files)
        for dir_node in self.dirs.values():
            num_dirs_subdir, num_files_subdir = dir_node.get_num_dirs_files()
            num_dirs += num_dirs_subdir
            num_files += num_files_subdir
        return num_dirs, num_files

    def create_backup_sets(self, set_writer, backup_paths):
        items = []

        path = self.get_full_path()
        size = self.get_size()

        DIR, FILE = 0, 1
        # When the path fits, we still cannot simply zip it up fully
        # since only a subset of entries may be in the backup_paths.
        if size <= self.upload_limit and self._is_inside_backup_paths(backup_paths, path):
            items.append((path, size, DIR))
        else:
            for file_, size in self.files:
                file_path = os.path.join(path, file_)
                if self._is_inside_backup_paths(backup_paths, file_path):
                    items.append((file_path, size, FILE))
            for dir_ in self.dirs.values():
                if dir_.get_size() > self.upload_limit:
                    dir_items = dir_.create_backup_sets(set_writer, backup_paths)
                    items.extend(dir_items)
                else:
                    dir_path = os.path.join(path, dir_.name)
                    if self._is_inside_backup_paths(backup_paths, dir_path):
                        items.append((dir_path, dir_.get_size(), DIR))
                    else:
                        dir_items = dir_.create_backup_sets(set_writer, backup_paths)
                        items.extend(dir_items)


        if self.parent is not None:
            return items

        if len(items) > 0:
            bins = binpacking.to_constant_volume(items, self.upload_limit, weight_pos=1)
            for bin_ in bins:
                paths = []
                total_size = 0
                num_dirs = 0
                num_files = 0
                for path, size, type_ in bin_:
                    paths.append(path)
                    total_size += size
                    if type_ == DIR:
                        node = self.get_node(path)
                        num_dirs_subdir, num_files_subdir = node.get_num_dirs_files()
                        num_dirs += num_dirs_subdir
                        num_files += num_files_subdir
                    else:
                        num_files += 1
                set_writer.write_set(paths, total_size, num_dirs, num_files)

        return None

class DualCounter:
    def __init__(self, title, name1, name2):
        self.title = title
        self.name1 = name1
        self.name2 = name2
        self.count1 = 0
        self.count2 = 0

    def add_counter1(self, count_):
        self.count1 += count_

    def add_counter2(self, count_):
        self.count2 += count_

    def verify(self):
        if self.count1 != self.count2:
            msg = (f"Mismatch: {self.title}: {self.name1}={self.count1}, "
                   f"{self.name2}={self.count2}, "
                   f"diff: {self.count1 - self.count2}")
            raise Exception(msg)

    def __add__(self, rhs):
        result = copy.deepcopy(self)
        result.count1 += rhs.count1
        result.count2 += rhs.count2
        return result

def update_find_counters(path, sub_num_files, sub_size_files):
    cmd = fr"find '{path}' \( -type f -o -type l \) | wc -l"
    output = subprocess.check_output(cmd, shell=True).decode()
    sub_num_files.add_counter2(int(output.strip()))
    sub_num_files.verify()

    cmd = ( fr"find '{path}' \( -type f -o -type l \) -printf '%s\n' "
            f"| awk '{{sum+=$1}} END {{print sum}}'")
    output = subprocess.check_output(cmd, shell=True).decode()
    output = output.strip()
    if len(output) == 0:
        size = 0
    else:
        size = int(output.strip())
    sub_size_files.add_counter2(size)
    sub_size_files.verify()

def crawl(snapshot_path, backup_paths, upload_limit):
    root_node = Path(snapshot_path, None, upload_limit)

    num_files = DualCounter('files', 'walk', 'find')
    size_files = DualCounter('size', 'walk', 'find')

    for path in backup_paths:
        path = os.path.join(snapshot_path, path)
        print('Crawling', path)

        sub_num_files = DualCounter('subfiles', 'walk', 'find')
        sub_size_files = DualCounter('subsize', 'walk', 'find')

        def process_file(node, file_path):
            info = os.lstat(file_path) # Do not follow symlinks
            sub_num_files.add_counter1(1) # pylint: disable=cell-var-from-loop
            file_size = info[stat.ST_SIZE]
            sub_size_files.add_counter1(file_size) # pylint: disable=cell-var-from-loop

            file_ = os.path.basename(file_path)
            node.add_file(file_, file_size)

        if not os.path.isdir(path):
            node = root_node.get_node(os.path.dirname(path))
            process_file(node, path)
        else:
            def raise_error(error):
                raise error

            for root, _, files in os.walk(path, topdown=False,
                                          onerror=raise_error, followlinks=False):

                node = root_node.get_node(root)
                for file_ in files:
                    process_file(node, os.path.join(root, file_))

        update_find_counters(path, sub_num_files, sub_size_files)

        num_files += sub_num_files
        size_files += sub_size_files

    num_files.verify()
    size_files.verify()

    return root_node

def glob_backup_paths(backup_paths_unglobbed, snapshot_path):
    backup_paths = []
    num_warnings = 0
    base_path = pathlib.Path(snapshot_path)
    for path_unglobbed in backup_paths_unglobbed:
        paths_globbed = tuple(base_path.glob(path_unglobbed))
        if len(paths_globbed) == 0:
            print(f"WARNING: Path {path_unglobbed} does not exist and will be ignored!")
            num_warnings += 1
        for path_globbed in paths_globbed:
            backup_paths.append(os.fspath(path_globbed.relative_to(base_path)))
    return backup_paths, num_warnings

def crawl_and_write(snapshot_path, backup_paths, upload_limit, state_file):
    root_node = crawl(snapshot_path, backup_paths, upload_limit)
    with open(state_file, 'wb') as f:
        pickle.dump(root_node, f)

def load(state_file, set_writer, backup_paths):
    with open(state_file, 'rb') as f:
        root_node = pickle.load(f)
    print('Total size of backed up files:', size_to_string(root_node.get_size()))
    root_node.create_backup_sets(set_writer, backup_paths)


if __name__ == '__main__':
    zfs_pool = os.environ['ZFS_POOL']
    backup_paths_unglobbed = tuple(map(os.path.normpath, sys.argv[1:]))
    snapshot_path = os.path.normpath(os.environ['SNAPSHOT_PATH'])
    state_file = os.environ['STATE_FILE']
    set_path = os.path.normpath(os.environ['SET_PATH'])
    upload_limit = int(os.environ['UPLOAD_LIMIT_MB']) * 1024 * 1024

    backup_paths, num_warnings = glob_backup_paths(backup_paths_unglobbed, snapshot_path)
    if num_warnings > 0 and os.environ.get('IGNORE_WARNINGS') != '1':
        res = input(f"{num_warnings} warning(s) encountered, see above."
                    " Proceed? (IGNORE_WARNINGS=1 skips this) [y/n] ").strip().lower()
        if res != 'y':
            print('ABORTED')
            sys.exit(1)

    # Save state after crawling file system, so can be resumed later
    crawl_and_write(snapshot_path, backup_paths, upload_limit, state_file)
    set_writer = SetWriter(snapshot_path, set_path, zfs_pool)
    load(state_file, set_writer, backup_paths)
