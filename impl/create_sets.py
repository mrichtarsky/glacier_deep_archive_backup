#!/usr/bin/env python

'''
Ran by do_backup_to_aws.sh

Parameters passed by environment, backup paths on command line.

Creates sets with UPLOAD_LIMIT maximum size,
stores the list of files in the set in distinct files in SET_PATH.
SNAPSHOT_PATH is where the ZFS snapshot is mounted.

Sets are processed later by upload_sets.py.
'''

import binpacking
import copy
import os
import pickle
import re
import stat
import subprocess
import sys

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

class SetWriter():
    def __init__(self, snapshot_path, set_path, zfs_pool):
        self.snapshot_path = snapshot_path
        self.set_path = set_path
        self.zfs_pool = zfs_pool

    def _make_archive_name(self, path):
        name = path.replace(self.snapshot_path, self.zfs_pool)
        name = re.sub('[^a-zA-Z0-9_-]', '_', name)
        return name

    def write_set(self, path, items, size):
        print(f"Set: {path=}, {len(items)} item(s), {size_to_string(size)}")
        archive_name = self._make_archive_name(path)

        counter = 0
        while True:
            set_fileName = os.path.join(self.set_path,
                                        f"{archive_name}_{counter:03d}")
            if not os.path.exists(set_fileName):
                break
            counter += 1

        with open(set_fileName, 'wt') as set_file:
            for item in items:
                print('  ', item)
                print(item, file=set_file)

class Path():
    def __init__(self, name, parent):
        self.parent = parent
        self.name = name
        self.files = []
        self.dirs = {}
        self.size = None # Lazily computed

    def add_file(self, name, size):
        self.files.append((name, size))

    def get_dir(self, name):
        return self.dirs.setdefault(name, Path(name, self))

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

    def create_backup_sets(self, upload_limit, set_writer):
        path = self.get_full_path()
        size = self.get_size()
        is_snapshot_path = self.parent is None
        if size <= upload_limit and not is_snapshot_path:
            # When the snapshot_path fits, we still cannot simply zip it up fully
            # since only a subset of entries may be in the backup_paths.
            # But we may do so when we are in a subdirectory.
            set_writer.write_set(path, (path, ), size)
        else:
            items = copy.copy(self.files)
            for dir_ in self.dirs.values():
                if dir_.get_size() > upload_limit:
                    dir_.create_backup_sets(upload_limit, set_writer)
                else:
                    items.append((dir_.name, dir_.get_size()))
            if len(items) > 0:
                bins = binpacking.to_constant_volume(items, upload_limit, weight_pos=1)
                for bin_ in bins:
                    size = sum(file_[1] for file_ in bin_)
                    items = [ os.path.join(path, item[0]) for item in bin_ ]
                    set_writer.write_set(path, items, size)

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
    cmd = f"find '{path}' -type f,l | wc -l"
    output = subprocess.check_output(cmd, shell=True).decode()
    sub_num_files.add_counter2(int(output.strip()))
    sub_num_files.verify()

    cmd = f"find '{path}' -type f,l -printf '%s\n' | awk '{{sum+=$1}} END {{print sum}}'"
    output = subprocess.check_output(cmd, shell=True).decode()
    output = output.strip()
    if len(output) == 0:
        size = 0
    else:
        size = int(output.strip())
    sub_size_files.add_counter2(size)
    sub_size_files.verify()

def crawl(snapshot_path, backup_paths):
    root_node = Path(snapshot_path, None)

    num_files = DualCounter('files', 'walk', 'find')
    size_files = DualCounter('size', 'walk', 'find')

    for path in backup_paths:
        path = os.path.join(snapshot_path, path)
        print('Crawling', path)

        sub_num_files = DualCounter('subfiles', 'walk', 'find')
        sub_size_files = DualCounter('subsize', 'walk', 'find')

        def process_file(file_path):
            info = os.lstat(file_path) # Do not follow symlinks
            sub_num_files.add_counter1(1) # pylint: disable=cell-var-from-loop
            file_size = info[stat.ST_SIZE]
            sub_size_files.add_counter1(file_size) # pylint: disable=cell-var-from-loop

            rel_file_path = file_path.replace(snapshot_path, '', 1).lstrip('/')
            path, name_ = os.path.split(rel_file_path)
            node = root_node
            if len(path) > 0:
                comps = path.split('/')
                for comp in comps:
                    node = node.get_dir(comp)
            node.add_file(name_, file_size)

        if not os.path.isdir(path):
            process_file(path)
        else:
            def raise_error(error):
                raise error

            for root, _, files in os.walk(path, topdown=False,
                                          onerror=raise_error, followlinks=False):
                for file_ in files:
                    file_path = os.path.join(root, file_)
                    process_file(file_path)

        update_find_counters(path, sub_num_files, sub_size_files)

        num_files += sub_num_files
        size_files += sub_size_files

    num_files.verify()
    size_files.verify()

    return root_node

def crawl_and_write(snapshot_path, backup_paths, state_file):
    root_node = crawl(snapshot_path, backup_paths)
    with open(state_file, 'wb') as f:
        pickle.dump(root_node, f)

def load(state_file, upload_limit, set_writer):
    with open(state_file, 'rb') as f:
        root_node = pickle.load(f)
    print('Total size of backed up files:', size_to_string(root_node.get_size()))
    root_node.create_backup_sets(upload_limit, set_writer)


if __name__ == '__main__':
    zfs_pool = os.environ['ZFS_POOL']
    backup_paths = sys.argv[1:]
    snapshot_path = os.environ['SNAPSHOT_PATH']
    state_file = os.environ['STATE_FILE']
    set_path = os.environ['SET_PATH']
    upload_limit = int(os.environ['UPLOAD_LIMIT_MB']) * 1024 * 1024

    # Save state after crawling file system, so can be resumed later
    crawl_and_write(snapshot_path, backup_paths, state_file)
    set_writer = SetWriter(snapshot_path, set_path, zfs_pool)
    load(state_file, upload_limit, set_writer)
