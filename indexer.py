
import hashlib
import json
import os
import shutil
import stat
import sys
from pathlib import Path


def sha256_file(path, chunk_size=1024 * 1024):
    """Calculate a file's SHA-256 hash without loading it all into memory."""
    digest = hashlib.sha256()

    with open(path, "rb") as file:
        while chunk := file.read(chunk_size):
            digest.update(chunk)

    return digest.hexdigest()


def build(rootfs, store):
    rootfs = Path(rootfs).resolve()
    store = Path(store).resolve()

    if not rootfs.is_dir():
        raise NotADirectoryError(f"Root filesystem not found: {rootfs}")

    if rootfs == store or rootfs in store.parents or store in rootfs.parents:
        raise ValueError("Root filesystem and store must be separate directories")

    blobs_dir = store / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)

    entries = {}
    unique_blobs = set()
    total_bytes = 0
    unique_bytes = 0

    for current_dir, dirnames, filenames in os.walk(
        rootfs, followlinks=False
    ):
        dirnames.sort()
        filenames.sort()

        current_path = Path(current_dir)

        for name in dirnames + filenames:
            path = current_path / name
            relative_path = "/" + path.relative_to(rootfs).as_posix()
            info = path.lstat()

            entry = {
                "mode": stat.S_IMODE(info.st_mode),
                "size": info.st_size,
            }

            if stat.S_ISDIR(info.st_mode):
                entry["type"] = "directory"

            elif stat.S_ISLNK(info.st_mode):
                entry["type"] = "symlink"
                entry["target"] = os.readlink(path)

            elif stat.S_ISREG(info.st_mode):
                digest = sha256_file(path)
                entry["type"] = "file"
                entry["sha256"] = digest

                total_bytes += info.st_size

                if digest not in unique_blobs:
                    destination = blobs_dir / digest

                    if not destination.exists():
                        shutil.copyfile(path, destination)

                    unique_blobs.add(digest)
                    unique_bytes += info.st_size

            else:
                # Device nodes, sockets and other special files are not
                # materialized by this initial prototype.
                entry["type"] = "unsupported"

            entries[relative_path] = entry

    index = {
        "version": 1,
        "files": entries,
    }

    index_path = store / "index.json"
    temporary_path = store / "index.json.tmp"

    with open(temporary_path, "w", encoding="utf-8") as file:
        json.dump(index, file, indent=2, sort_keys=True)

    os.replace(temporary_path, index_path)

    duplicate_bytes = total_bytes - unique_bytes

    print(f"Root filesystem: {rootfs}")
    print(f"Store:           {store}")
    print(f"Entries indexed: {len(entries)}")
    print(f"Unique blobs:    {len(unique_blobs)}")
    print(f"Original bytes:  {total_bytes:,}")
    print(f"Unique bytes:    {unique_bytes:,}")
    print(f"Duplicate bytes: {duplicate_bytes:,}")
    print(f"Index written:   {index_path}")

    return index


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python indexer.py ROOTFS STORE")
        sys.exit(2)

    build(sys.argv[1], sys.argv[2])
