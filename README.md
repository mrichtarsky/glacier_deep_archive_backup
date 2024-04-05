# Introduction

**Note: If you are relying on this for your last-resort backup, please read everything below!**

`glacier_deep_archive_backup` is a backup solution that uses [S3 Glacier Deep Archive](https://aws.amazon.com/blogs/aws/new-amazon-s3-storage-class-glacier-deep-archive/) for storage. This is the most cost effective cloud backup storage I'm aware of. The use case is for full, off-site, encrypted backups that are only retrieved after a catastrophe (i.e. your house burns down, RAID *and* local backups are gone). This is reflected by the [cost structure](https://aws.amazon.com/s3/pricing/) and properties of Deep Archive:

- Upload is free
- Storage is $0.00099 per GiB/month ($1.01376/TiB). That makes it very cheap to store your data.
- Restore and download is quite costly:
    - Restore from S3 tape to S3 blob:
        - $0.0025/GiB ($2.56/TiB) for Bulk within 48 hours
        - $0.02/GiB ($20.48/TiB) for Standard within 12 hours
    - Download: The first 100 GiB/month are free, then 10 TiB/Month for $0.09 per GiB ($92.16/TiB) and discounts for more.
    - **Update:** With the [European Data Act coming into force](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=OJ:L_202302854&qid=1709707090487#d1e3746-1-1), the cost structure likely changes significantly: Egress fees are no longer permitted, and [AWS has admitted as much](https://aws.amazon.com/blogs/aws/free-data-transfer-out-to-internet-when-moving-out-of-aws/), although they are tying this to "moving out of AWS" (without checking). So while you shouldn't rely on it, it's probably safe to assume that you won't need to pay the download fees above, which are 90% of the recovery costs, so for bulk you only pay $2.56/TiB.

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
- `zstd`
- `gpg`
- `pip install binpacking`
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- An AWS account and an S3 bucket. Follow [these instructions](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-prereqs.html) to set up an account and put the credentials in `~/.aws/credentials`. The file should look similar to this:

    ```
    [default]
    aws_access_key_id = XESRNTIERINVALIDNSTIENRSITE
    aws_secret_access_key = inewtn8un3invalid289tneirnst
    region = us-east-2
    ```

    `region` is where your S3 bucket is located. Since this is an off-site backup, it should *not* be the region most closest to you. Best to choose a different continent ;)

- `pip install pytest` if you want to run the tests

## Installation

Just clone, or download and unzip.

```
git clone git@github.com:mrichtarsky/glacier_deep_archive_backup.git
```

```
wget https://github.com/mrichtarsky/glacier_deep_archive_backup/archive/refs/heads/main.zip
unzip main.zip
rm main.zip
```


# Usage

## Backup

- `cp config/backup_example.sh config/backup.sh` and edit `backup.sh` to reflect your setup. In `BACKUP_PATHS`, list all the files and directories that will be backed up recursively. Make sure to use double quotes to escape names that `"have spaces"` etc. You can use wildcards, see the notes in the file for details.
- Edit `config/passphrase.txt` and put a strong passphrase there. Your data will be encrypted locally using this password before upload. **Make sure this file is only readable/writable by you!**

- Run `./backup_scratch config/backup.sh` to start the backup. Logs are in `logs/backup_scratch.log`.
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
tank_pics_000.list_contents.tar.zstd.gpg

# The list of dirs/files stored in the archive,
# as passed to tar. Will not recursively list files of
# a directory if that directory was passed to tar.

tank_pics_000.tar.zstd.gpg
# The tar archive, zstd compressed, aes256 encrypted
```

You should store everything you need to restore the backup in a safe location:

- Your passphrase
- Credentials to access AWS

Consider you want to be able to retrieve these backups when your house has burnt down.

You should also test a full backup/restore cycle once. It can involve a small subset of data, e.g. 10 GiB, which will make the test cheap.

## Restore

- `cp config/restore_example.sh config/restore.sh` and edit `restore.sh` to reflect your setup
- Run `./restore config/restore.sh`. This attempts to restore all files to the location specified.
- Note that it is not possible to directly download files from Deep Archive: First you need to schedule an archive for restore, which will basically retrieve it from tape and put it in S3 blob storage on AWS side. Then it can be downloaded. The script automates all this, including:
    - Waiting for the file to become available
    - Downloading
    - Decryption
    - Extraction
- Restore will incur costs for restore and transfer. See above for a detailed breakdown.

Should you wish to only restore some files to save time or money you can follow these manual steps:

- In the S3 web interface select the file and initiate restore. You can choose between Standard and Bulk retrieval (Bulk $2.56/TiB, Standard $20.48/TiB).
- Once the file is available, download it
- Use `./extract_archive ARCHIVE DEST_PATH` to decrypt and extract it (e.g. `./extract_archive tank_pics_000.tar.zstd.gpg /tank_restore`)

# Misc

- Data is encrypted using [`gpg`](https://www.gnupg.org/) (`AES256` cipher)

- Incremental backups are not supported

- In addition to the costs above, there are these small additional charges:
    - $0.10/1000 requests for data retrieval
    - $0.05/1000 PUT, COPY, POST, LIST requests
    - $0.0004/1000 GET, SELECT, and all other requests
    - 8 KB overhead/file billed at S3 standard storage rates
    - 32 KB overhead/file billed at Deep Archive rates

    The script creates archives, so the number of files stored is very small. For an upload limit of 50 GiB, you would get 40 files/TiB. So these costs are negligible. Most other backup solutions operate 1:1 at a file level, causing tremendous costs (see alternatives below).

    You can check the full details on the [pricing page](https://aws.amazon.com/s3/pricing/).

- Symlinks are *not* followed. Therefore, links pointing to files not covered by the paths backed up will not be considered!

- No file splitting: The largest file must fit into `UPLOAD_LIMIT_MB`. This is checked at the beginning.

- There is a progress display which works as follows:

    ```
    Elapsed: 10h 38m, Archived: 0.25/2.71 TiB (9.2%, 17.49 MiB/s), Uploaded: 0.12/2.71 TiB (4.6%, 5.12 MiB/s), Ratio: 1.0x, ETA: 7d 8h 39m - 7d 20h 2m
    ```

    - Uploaded bytes are gross (before compression)
    - Upload speed is for net uploaded bytes (after compression)
    - Ratio shows the achieved compression
    - For ETA, a range is estimated:
      - The lower bound assumes that the achieved compression rate remains the same for the remaining files
      - The upper bound assumes that compression is 1x for the remaining files
      - It's possible that the lower bound is still too high when the remaining files compress better

- Backup files are not deleted in your S3 bucket, you need to take care of this yourself. There is an [`expire`](https://github.com/mrichtarsky/glacier_deep_archive_backup/blob/main/expire) script you can use for that.

- Since typically archive building is faster than upload, archive building will create the next archive in the background, so that the upload can run continuously after the ramp up. This leads to about 25% reduction of elapsed time on my server/connection.

- Discussed on [Hacker News](https://news.ycombinator.com/item?id=32864052)

# Alternatives

There are other backup solutions that can target Deep Glacier:

- [`duplicity`](https://duplicity.gitlab.io/) - This is a great tool if you want to have incremental backups. It will use rsync-style signatures to only transmit differences of files, and therefore can perform very efficient incremental backups. At the same time, it creates `tar` archives,
  so does not sacrifice efficiency on Deep Archive. I would probably not have written the tool here had I been aware of this earlier ;) The only downside is that restore does not recover files  from Deep Archive automatically, you have to do that manually, and wait for availability, before any operations like `verify` or `restore` can work.
  If you only care about full backups, `duplicity` does not buy you much, in fact it will cause some overhead due to the signatures stored both locally and on the backup, which are only needed for incremental backups.

- [`rclone`](https://rclone.org/)
- [`aws sync`](https://awscli.amazonaws.com/v2/documentation/api/latest/reference/s3/sync.html)

   Both tools store files 1:1 in Deep Archive. Due to the cost structure this is prohibitively expensive, and the reason why `glacier_deep_archive_backup` creates archives. `aws sync` only supports server-side encryption, instead of client-side as done by this script. It cannot restore files automatically out of Deep Archive, while for `rclone` it's a manual step. This script does it automatically and also waits for the files to become available, to get the restore done as fast as possible.
- [Arq](https://www.arqbackup.com/) - Only supports Windows/macOS. Apart from that it looks pretty good, the amount of files created is already 40 for ~700 MiB of data, but it probably scales much better than a 1:1 copy. Also able to restore single files, and automatically requests restores and waits for them.

# ToDo

- Store timestamp and config for resume
- Do away with scratch/resume split and just pick off where we left off?
- Add test for case where archiver thread runs into error

- Add automated E2E test
    - Also for insufficient disk space

- Wrap tar, count lines and show progress bar
  - Show a file every second
  - Delete that line
  - [1/200] /path/to/file size

- Increase zstd compression/make configurable
  - Compression rarely uses CPU, gpg takes up the majority. So raising it would be possible. On the other hand, when backing up videos/photos, it won't make much difference.

- Version

- Just append to one logfile
- Option for quieter output, log full output to file
- Option to retrieve file lists only
- Preserve permissions/untar options
