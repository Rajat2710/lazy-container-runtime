import argparse
import hashlib
import json
import os
import posixpath
import stat
import time
import urllib.request
from pathlib import Path

from fuse import FUSE, FuseOSError, Operations


class LazyFilesystem(Operations):
    def __init__(self, store_dir, cache_dir, server_url):
        self.store_dir = Path(store_dir).resolve()
        self.cache_dir = Path(cache_dir).resolve()
        self.server_url = server_url.rstrip("/")

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        index_path = self.store_dir / "index.json"
        with open(index_path, encoding="utf-8") as file:
            self.entries = json.load(file)["files"]

        # Build a directory-to-children lookup for readdir().
        self.children = {"/": set()}

        for path in self.entries:
            parent = posixpath.dirname(path)
            name = posixpath.basename(path)

            self.children.setdefault(parent, set()).add(name)

            if self.entries[path]["type"] == "directory":
                self.children.setdefault(path, set())

    def _normalize(self, path):
        return posixpath.normpath("/" + path.lstrip("/"))

    def _entry(self, path):
        path = self._normalize(path)

        if path == "/":
            return {"type": "directory", "mode": 0o755, "size": 0}

        entry = self.entries.get(path)

        if entry is None:
            raise FuseOSError(2)  # ENOENT: no such file or directory

        return entry

    def _cache_path(self, digest):
        return self.cache_dir / digest

    def _fetch_blob(self, digest):
        cached_path = self._cache_path(digest)

        if cached_path.is_file():
            print(f"CACHE HIT: {digest}", flush=True)
            return cached_path

        url = f"{self.server_url}/blobs/{digest}"
        temporary_path = self.cache_dir / f"{digest}.part"

        print(f"CACHE MISS: downloading {digest}", flush=True)

        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                with open(temporary_path, "wb") as output:
                    while chunk := response.read(64 * 1024):
                        output.write(chunk)

            # Verify the downloaded bytes before trusting the cache.
            actual_hash = hashlib.sha256(
                temporary_path.read_bytes()
            ).hexdigest()

            if actual_hash != digest:
                raise ValueError(
                    f"Hash mismatch: expected {digest}, got {actual_hash}"
                )

            os.replace(temporary_path, cached_path)
            print(f"CACHED: {digest}", flush=True)
            return cached_path

        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def getattr(self, path, fh=None):
        entry = self._entry(path)
        file_type = entry["type"]
        permissions = entry.get("mode", 0o755)

        if file_type == "directory":
            mode = stat.S_IFDIR | permissions
        elif file_type == "file":
            mode = stat.S_IFREG | permissions
        elif file_type == "symlink":
            mode = stat.S_IFLNK | 0o777
        else:
            raise FuseOSError(95)  # EOPNOTSUPP

        now = time.time()

        return {
            "st_mode": mode,
            "st_nlink": 2 if file_type == "directory" else 1,
            "st_size": entry.get("size", 0),
            "st_ctime": now,
            "st_mtime": now,
            "st_atime": now,
            "st_uid": os.getuid(),
            "st_gid": os.getgid(),
        }

    def readdir(self, path, fh):
        entry = self._entry(path)

        if entry["type"] != "directory":
            raise FuseOSError(20)  # ENOTDIR

        normalized = self._normalize(path)
        return [".", "..", *sorted(self.children.get(normalized, set()))]

    def open(self, path, flags):
        entry = self._entry(path)

        if entry["type"] != "file":
            raise FuseOSError(21)  # EISDIR for directories

        # Deliberately do not download here.
        # Fetching happens when the program actually reads file data.
        return 0

    def read(self, path, size, offset, fh):
        entry = self._entry(path)

        if entry["type"] != "file":
            raise FuseOSError(22)  # EINVAL

        cached_path = self._fetch_blob(entry["sha256"])

        with open(cached_path, "rb") as file:
            file.seek(offset)
            return file.read(size)

    def readlink(self, path):
        entry = self._entry(path)

        if entry["type"] != "symlink":
            raise FuseOSError(22)

        return entry["target"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Mount a filesystem that fetches file blobs on demand"
    )
    parser.add_argument("store", help="Directory containing index.json")
    parser.add_argument("mountpoint", help="Empty directory for the mount")
    parser.add_argument(
        "--cache", default="cache",
        help="Local blob cache directory (default: cache)",
    )
    parser.add_argument(
        "--server", default="http://127.0.0.1:8000",
        help="Blob server base URL",
    )
    args = parser.parse_args()

    mountpoint = Path(args.mountpoint).resolve()
    if not mountpoint.is_dir():
        parser.error(f"Mountpoint must be an existing directory: {mountpoint}")

    print(f"Index:      {Path(args.store).resolve() / 'index.json'}")
    print(f"Mountpoint: {mountpoint}")
    print(f"Cache:      {Path(args.cache).resolve()}")
    print(f"Blob server: {args.server}")

    filesystem = LazyFilesystem(args.store, args.cache, args.server)

    FUSE(
        filesystem,
        str(mountpoint),
        foreground=True,
        ro=True,
        allow_other=True,
    )
