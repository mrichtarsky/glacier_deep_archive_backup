# Introduction

`glacier_deep_archive_backup` is a backup solution that uses [S3 Glacier Deep Archive](https://aws.amazon.com/blogs/aws/new-amazon-s3-storage-class-glacier-deep-archive/) for storage. This is the most cost effective cloud backup storage I'm aware of. The use case is for full, off-site, encrypted backups that are only retrieved after a catastrophe (i.e. your house burns down, RAID *and* local backups are gone). This is reflected by the [cost structure](https://aws.amazon.com/s3/pricing/) and properties of Deep Archive:

- Upload is free
- Storage is $0.00099 per GiB/month ($1.01376/TiB). That makes it very cheap to store your data.
- Restore and download is quite costly:
    - Restore from S3 tape to S3 blob:
        - $0.0025/GiB ($2.56/TiB) for Bulk within 48 hours
        - $0.02/GiB ($20.48/TiB) for Standard within 12 hours
    - Download: The first 100 GiB/month are free, then 10 TiB/Month for $0.09 per GiB ($92.16/TiB) and discounts for more.

- The assumption is that you will hopefully never need to restore ;) (and for testing, you can use the 100 GiB free download where you only pay for restore)
- Note: The minimum storage charge is for 180 days. So even if you delete your data earlier, you still pay. So this backup solution is not meant for fast-changing data, but it allows you to retain quite a few full backups that you can restore directly without applying several incremental backups on top.
- Data is stored across 3 or more AWS Availability Zones, providing [99.999999999% data durability](https://aws.amazon.com/s3/storage-classes/#____)
- All quoted prices are for US East, some regions are more expensive

The script relies on the ZFS file system's snapshot capabilities to provide a consistent state of the file system during upload (which can take days). So this will only work for data on ZFS pools, although it could be adapted to work for other snapshotting file systems or by losening the consistency requirements.

# Installation

## Prerequisites

- Unix-ish system
- ZFS pool(s)
- Recent python
- `tar` with `zstd` support
- `pip install binpacking`
- `unbuffer` (part of `expect` package, Ubuntu/Debian: `sudo apt install expect`)
- `pip install pytest` if you want to run the tests
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- An AWS account and an S3 bucket. Follow [these instructions](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-prereqs.html) to set up an account and put the credentials in `~/.aws/credentials`. The file should look similar to this:

    ```
    [default]
    aws_access_key_id = XESRNTIERINVALIDNSTIENRSITE
    aws_secret_access_key = inewtn8un3invalid289tneirnst
    region = us-east-2
    ```

    `region` is where your S3 bucket is located. Since this is an off-site backup, it should *not* be the region most closest to you. Best to choose a different continent ;)

## Installation

Just clone, or download and unzip.

```
git clone git@github.com:mrichtarsky/glacier_deep_archive_backup.git
```

```
wget https://github.com/mrichtarsky/glacier_deep_archive_backup/archive/refs/heads/main.zip
unzip main.zip
```


# Usage

## Backup

- `cp config/backup_example.sh config/backup.sh` and edit `backup.sh` to reflect your setup. In `BACKUP_PATHS`, list all the files and directories that will be backed up recursively. Make sure to use double quotes to escape names that `"have spaces"` etc.
- Edit `config/passphrase.txt` and put a strong passphrase there. Your data will be encrypted locally using this password before upload. *Make sure this file is only readable/writable by you!*
- Run `./backup_scratch.sh config/backup.sh` to start the backup. Logs are in `logs/backup_scratch.log`.
- If the backup fails for some reason (e.g. internet down), use `./backup_resume config/backup.sh TIMESTAMP` to resume.
    - `TIMESTAMP` is the timestamp displayed by the earlier scratch backup run
    - Logs are in `logs/backup_resume.log`
- If you have several pools, simply create a separate `backup_poolname.sh` for each and do a corresponding scratch backup (e.g. `./backup_scratch backup_tank.sh`).

An unprivileged user can run the scripts. Make sure this user has all the necessary permissions for reading the data to be backed up.  Only the following operations are executed as `root`, for which the user must have `sudo` privileges:
- Creating, mounting, unmounting and destroying the ZFS snapshot
- Creating the path for the snapshot mount
- For the fuzz test, creating a `tmpfs`

Backups are saved in your S3 bucket in a timestamped directory (e.g. `2022-02-18-195831`). This directory contains pairs of files:

```
tank_pics_000_contents.txt  # The list of dirs/files stored in the archive,
                            # as passed to tar. Will not recursively list files of
                            # a directory if that directory was passed to tar.
tank_pics_000.tar.zstd.ssl  # The tar archive, zstd compressed, aes256 encrypted
```

## Restore

- `cp config/restore_example.sh config/restore.sh` and edit `restore.sh` to reflect your setup
- Run `./restore config/restore.sh`. This attempts to restore all files to the location specified.
- Note that it is not possible to directly download files from Deep Archive: First you need to schedule an archive for restore, which will basically retrieve it from tape and put it in S3 blob storage on AWS side. Then it can be downloaded. The script automates all this, including decryption and extraction after download.
- Restore will incur costs for restore and transfer. See above for a detailed breakdown.

Should you wish to only restore some files to save time or money you can follow these manual steps:

- In the S3 web interface select the file and initiate restore. You can choose between Standard and Bulk retrieval (Bulk $2.56/TiB, Standard $20.48/TiB).
- Once the file is available, download it
- Use `./extract_archive ARCHIVE DEST_PATH` to decrypt and extract it (e.g. `./extract_archive tank_pics_000.tar.zstd.ssl /tank_restore`)

# Misc

- Symlinks are *not* followed. Therefore, links pointing to files not covered by the paths backed up will not be considered!
- No file splitting: The largest file must fit into `UPLOAD_LIMIT_MB`.
- Backup files are not deleted in your S3 bucket, you need to take care of this yourself

# ToDo

- Add automated E2E test
- Just append to one logfile
- Store timestamp and config for resume
- Do away with scratch/resume split and just pick off where we left off?
- Estimate time
- Show net, gross size
- Option for quieter output, log full output to file
