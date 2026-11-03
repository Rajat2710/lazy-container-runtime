# Lazy Container Runtime: On-Demand Filesystem Prototype

A Python/FUSE proof of concept for serving filesystem file contents as content-addressed blobs over HTTP and fetching them into a local cache only when a process reads them.

> **Status:** Prototype. This project demonstrates lazy reads from a filesystem that has already been exported and indexed. It does **not** yet implement native Docker/containerd registry-backed lazy pulling or individual-file retrieval directly from compressed Docker image layers.

## Contents

- [What it demonstrates](#what-it-demonstrates)
- [Architecture](#architecture)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Install](#install)
- [Quick start: Python image](#quick-start-python-image)
- [Run the server and mount in separate terminals](#run-the-server-and-mount-in-separate-terminals)
- [Verify lazy fetch and cache behavior](#verify-lazy-fetch-and-cache-behavior)
- [Test with a custom filesystem](#test-with-a-custom-filesystem)
- [Test with Ubuntu 24.04](#test-with-ubuntu-2404)
- [Run a process/container using the mount](#run-a-processcontainer-using-the-mount)
- [Benchmarks](#benchmarks)
- [How each component works](#how-each-component-works)
- [Troubleshooting](#troubleshooting)
- [Limitations and next steps](#limitations-and-next-steps)

## What it demonstrates

- Exporting a container image filesystem for a test image.
- Indexing directories, symlinks, and regular files.
- Computing SHA-256 hashes for regular-file contents.
- Storing unique file contents as blobs under `STORE/blobs/<sha256>`.
- Serving blobs over HTTP.
- Exposing the indexed filesystem through a read-only FUSE mount.
- Fetching file contents on the first read, verifying the digest, and caching the content.
- Reusing cached content on subsequent reads.
- Running a process using the mounted filesystem, including an OCI-runtime demonstration with `runc`.

The project also has an optional simulated bandwidth and latency feature in the HTTP blob server.

## Architecture

```text
Image already available through Docker
             |
             v
  make_store.sh (docker pull/create/export)
             |
             v
     exported rootfs/
             |
             v
      indexer.py
       |       |
       |       +--> store/index.json (paths, type, mode, size, SHA-256)
       |
       +----------> store/blobs/<sha256> (unique file contents)
                              |
                              v
                       blob_server.py
                         HTTP /blobs/<sha256>
                              |
                              v
Application reads a path --> LazyFS (FUSE)
                              |
                      Look up path in index
                              |
                      Is blob cached locally?
                         /          \
                       yes           no
                        |             |
                    CACHE HIT     HTTP download
                                      |
                               SHA-256 verification
                                      |
                               atomic cache write
                                      |
                                 CACHED blob
```

**Important:** `index.json` is local metadata in this prototype. The file contents are fetched from the local HTTP blob server on first read. The index is built from an already-exported root filesystem; this is not yet a Docker Registry manifest/layer resolver.

## Repository layout

| Path | Purpose |
|---|---|
| `indexer.py` | Walks a root filesystem, records metadata, computes hashes, and writes unique file contents to a blob store. |
| `blob_server.py` | HTTP server that serves blobs by SHA-256 digest; can simulate latency and bandwidth limits. |
| `lazyfs.py` | Read-only FUSE filesystem. It reads `index.json`, resolves paths, downloads missing blobs, verifies hashes, and caches them. |
| `make_store.sh` | Pulls a Docker image, creates a temporary container, exports its filesystem, then invokes the indexer. |
| `bench.py` | Benchmarks ten large unique files from the Python image against direct, cold-cache, and warm-cache reads. |
| `bench_ubuntu.py` | Same benchmark pattern for the Ubuntu image. |
| `requirements.txt` | Python package dependencies (check this file in the repository for the pinned/current dependency list). |
| `oci-bundle/config.json` | OCI runtime configuration used by the `runc` demo, if included in the checkout. |
| `rootfs/`, `store/`, `mnt/`, `cache/` | Generated Python-image root filesystem, content-addressed store, mountpoint, and cache. These are generated locally and should not be committed. |
| `ubuntu-24.04-rootfs/`, `ubuntu-24.04-store/`, `ubuntu-mnt/`, `ubuntu-cache/` | Equivalent generated Ubuntu test data; should not be committed. |

## Prerequisites

Tested in a Linux/WSL environment with Docker Desktop integration and FUSE available.

- Linux or WSL2 with a working Linux distribution.
- Python 3.10+ (use the Python version supported by the checked-out dependencies; the prototype was tested with Python 3.12 in the current environment).
- Docker CLI connected to a working Docker Engine.
- FUSE userspace support and `/dev/fuse`.
- `runc` for the OCI container demonstration (optional for filesystem-only tests).

On Ubuntu/WSL, install common system packages:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip fuse3 git docker.io
```

If you use Docker Desktop with WSL integration, use its working Docker CLI/engine rather than installing a second daemon unnecessarily. Check:

```bash
docker version
python3 --version
```

For the OCI demo, also check:

```bash
runc --version
```

### FUSE configuration

The current implementation passes `allow_other=True` to FUSE. On systems where FUSE requires it, enable `user_allow_other` in `/etc/fuse.conf`:

```bash
grep -n 'user_allow_other' /etc/fuse.conf
```

If the line is commented out, edit the file with an editor and uncomment `user_allow_other`. This is a system-level setting; only change it if appropriate for your machine.

## Install

From the repository root:

```bash
cd ~/lazy-container-runtime
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install the Python dependencies required by the code. The FUSE binding imported by `lazyfs.py` is `fusepy`; if it is not already listed in `requirements.txt`, install it explicitly:

```bash
python -m pip install fusepy
```

If `requirements.txt` contains dependencies, install them too:

```bash
python -m pip install -r requirements.txt
```

Check that the FUSE binding imports:

```bash
python -c "from fuse import FUSE, Operations; print('fusepy import OK')"
```

> Depending on your Linux/FUSE setup, you may need the appropriate system FUSE library in addition to the Python package.

## Quick start: Python image

Run these commands from the repository root:

```bash
cd ~/lazy-container-runtime
source .venv/bin/activate
```

Prepare the default `python:3.11-slim` image:

```bash
bash make_store.sh python:3.11-slim
```

For this exact image tag, the script uses:

- `rootfs/` for the exported filesystem
- `store/` for `index.json` and `blobs/`

The script refuses to overwrite an existing `rootfs/` or `store/`. This protects existing data. If they already exist and you want to rebuild, back them up or move them aside first; do not blindly delete data you need.

The script runs `docker pull`, creates a temporary container, exports its merged filesystem with `docker export`, removes the temporary container, then runs `indexer.py`.

Expected indexer summary includes fields similar to:

```text
Root filesystem: ...
Store:           ...
Entries indexed: ...
Unique blobs:    ...
Original bytes:  ...
Unique bytes:    ...
Duplicate bytes: ...
Index written:   .../store/index.json
```

The exact counts vary by image version and platform.

## Run the server and mount in separate terminals

Keep the project root as the working directory in **each** terminal. Activate `.venv` in each terminal if needed.

Create mount and cache directories if they do not exist:

```bash
mkdir -p mnt cache
```

### Terminal 1 — start the blob server

```bash
cd ~/lazy-container-runtime
source .venv/bin/activate
python blob_server.py store --host 127.0.0.1 --port 8000
```

Expected startup message resembles:

```text
Serving blobs from /.../store/blobs at http://127.0.0.1:8000
```

Leave this terminal running. It prints `FETCH <sha256> (<bytes> bytes)` when a blob is served. Stop it with `Ctrl+C` when finished.

### Terminal 2 — mount LazyFS

```bash
cd ~/lazy-container-runtime
source .venv/bin/activate
python lazyfs.py store mnt --cache cache --server http://127.0.0.1:8000
```

Leave this terminal running in the foreground. It prints the index, mountpoint, cache, and server URL. On file reads, look for `CACHE MISS`, `CACHED`, and `CACHE HIT` messages.

### Terminal 3 — read files through the mount

Use a third terminal:

```bash
cd ~/lazy-container-runtime
ls mnt
cat mnt/etc/os-release
```

The `cat` command is only an example; choose a path that exists in the selected image. The first actual content read for an uncached file should trigger a fetch. Directory listings and metadata lookups use the index and normally do not download file contents.

### Stop and unmount

Stop the foreground LazyFS process with `Ctrl+C`. If the mount remains attached, unmount it:

```bash
fusermount3 -u mnt
```

On some systems the command is `fusermount -u mnt`. Do not remove a mountpoint or its cache while it is still mounted/in use.

## Verify lazy fetch and cache behavior

Pick a known regular file. For the Python image, examples may include:

```bash
ls mnt/usr/local/bin
cat mnt/etc/os-release
```

Use a file that exists in your image and that you have not already read through the mount.

1. Read the file through the mount.
2. In the LazyFS terminal, confirm `CACHE MISS: downloading <digest>` followed by `CACHED: <digest>`.
3. In the blob server terminal, confirm `FETCH <digest> (<size> bytes)`.
4. Read the same file again. LazyFS should print `CACHE HIT: <digest>`, and the server should not need to serve that blob again.

### Inspect the index and verify hashes

Show the first part of the index:

```bash
python - <<'PY'
import json
from pathlib import Path

index = json.loads(Path("store/index.json").read_text())["files"]
for path in list(sorted(index))[:20]:
    print(path, index[path])
PY
```

For a selected path, look up its SHA-256 and compare the source file, stored blob, and cache entry. Replace the example path with one that exists:

```bash
RELATIVE_PATH="usr/lib/os-release"
HASH=$(python - "$RELATIVE_PATH" <<'PY'
import json, sys
from pathlib import Path
path = "/" + sys.argv[1].lstrip("/")
index = json.loads(Path("store/index.json").read_text())["files"]
print(index[path]["sha256"])
PY
)

echo "Expected hash: $HASH"
sha256sum "rootfs/$RELATIVE_PATH" "store/blobs/$HASH"
```

After reading that file through LazyFS, verify the cache copy too:

```bash
sha256sum "cache/$HASH"
```

The hashes should match. The `store/blobs/` file is the source blob served by HTTP; `cache/` holds the downloaded copy.

### Force a controlled cache miss

Only do this when the mount is idle and you have selected the correct digest. Remove one cached blob, not the entire cache:

```bash
rm -f "cache/$HASH"
cat "mnt/$RELATIVE_PATH"
```

The next read should fetch and cache that blob again. Do not delete cache files while a read is in progress.

### Test deduplication

If two different paths have identical content, the index will point both paths to the same SHA-256 blob. The indexer stores one blob per unique digest. You can inspect repeated hashes with:

```bash
python - <<'PY'
import json
from collections import defaultdict
from pathlib import Path

entries = json.loads(Path("store/index.json").read_text())["files"]
paths_by_hash = defaultdict(list)
for path, entry in entries.items():
    if entry.get("type") == "file":
        paths_by_hash[entry["sha256"]].append(path)

for digest, paths in paths_by_hash.items():
    if len(paths) > 1:
        print(digest, "=>", paths)
PY
```

If no repeated hashes exist in a small custom filesystem, that is normal.

## Test with a custom filesystem

This test does not need Docker. It is useful for understanding the index, blobs, directories, symlinks, hashing, and cache behavior with files you control.

Use separate names so you do not overwrite the Python or Ubuntu stores:

```bash
cd ~/lazy-container-runtime
mkdir -p demo-rootfs/etc demo-rootfs/usr/bin demo-mnt demo-cache
printf 'hello from LazyFS\n' > demo-rootfs/etc/demo.txt
printf '#!/bin/sh\necho custom lazy filesystem\n' > demo-rootfs/usr/bin/demo
chmod +x demo-rootfs/usr/bin/demo
ln -s /etc/demo.txt demo-rootfs/demo-link
```

Build the store:

```bash
python indexer.py demo-rootfs demo-store
```

The output should report indexed entries, unique blobs, byte totals, and the generated `demo-store/index.json`.

Start the server in **Terminal 1**:

```bash
python blob_server.py demo-store --port 8002
```

Mount it in **Terminal 2**:

```bash
python lazyfs.py demo-store demo-mnt --cache demo-cache --server http://127.0.0.1:8002
```

Read it in **Terminal 3**:

```bash
cat demo-mnt/etc/demo.txt
cat demo-mnt/etc/demo.txt
cat demo-mnt/demo-link
```

Expected content:

```text
hello from LazyFS
hello from LazyFS
hello from LazyFS
```

The first read of `etc/demo.txt` should miss and fetch; the second should hit cache. Reading the symlink target should access the same underlying file content, so it should reuse the same digest cache entry.

To inspect the custom index and blob store:

```bash
cat demo-store/index.json
find demo-store -maxdepth 2 -type f -printf '%p %s bytes\n' | sort
find demo-cache -maxdepth 1 -type f -printf '%f %s bytes\n' | sort
```

When done, stop the LazyFS process, unmount `demo-mnt` if needed, and stop the server:

```bash
fusermount3 -u demo-mnt 2>/dev/null || true
```

The custom demo is a good way to validate behavior without pulling a full image. It does not need to be run from a container.

## Test with Ubuntu 24.04

The store builder derives output paths from the image tag by replacing unsafe characters with hyphens. For `ubuntu:24.04`, the expected directories are:

- `ubuntu-24.04-rootfs/`
- `ubuntu-24.04-store/`

Build them only if they do not already exist:

```bash
bash make_store.sh ubuntu:24.04
```

The script deliberately refuses to overwrite existing output directories. If your Ubuntu store already exists, use it rather than rerunning this command.

Create mount/cache directories:

```bash
mkdir -p ubuntu-mnt ubuntu-cache
```

### Terminal 1 — Ubuntu blob server

```bash
python blob_server.py ubuntu-24.04-store --port 8001
```

### Terminal 2 — Ubuntu LazyFS mount

```bash
python lazyfs.py ubuntu-24.04-store ubuntu-mnt \
  --cache ubuntu-cache \
  --server http://127.0.0.1:8001
```

### Terminal 3 — read Ubuntu files

```bash
cat ubuntu-mnt/etc/os-release
ls ubuntu-mnt/usr/bin
```

Expected `/etc/os-release` output identifies Ubuntu 24.04 LTS; the exact point release can vary.

To test a cache miss, select a file whose digest is not already in `ubuntu-cache/`. Read it once and observe the LazyFS/server logs, then read it again to see the cache hit.

## Run a process/container using the mount

### Simple `chroot` smoke test

If `ubuntu-mnt` is mounted and the required binaries and libraries are available:

```bash
cd ~/lazy-container-runtime
sudo chroot "$PWD/ubuntu-mnt" /bin/sh -c \
  'echo "Process running from LazyFS rootfs"; cat /etc/os-release'
```

This executes a process with the mounted filesystem as its root. **`chroot` by itself is not a container runtime**; it is only a useful filesystem execution smoke test.

### OCI runtime with `runc`

The repository's `oci-bundle/config.json` was used for an OCI-runtime demonstration. The configuration must point its root filesystem to the mounted Ubuntu filesystem. In this project's current layout, the configured root path is `../ubuntu-mnt`, relative to `oci-bundle/`.

Ensure the Ubuntu LazyFS mount is active first. Then run:

```bash
cd ~/lazy-container-runtime/oci-bundle
sudo runc run lazyfs-demo
```

The demo process prints a message and the Ubuntu release information, then exits. The exact output depends on the process arguments in `config.json`.

This demonstrates an OCI runtime launching a container using the LazyFS mount as its root filesystem. It does **not** mean that `docker run` natively uses LazyFS, and it does not demonstrate registry-backed lazy pulling.

If the container fails, inspect `oci-bundle/config.json`, confirm the `root.path` resolves to the active mount, and verify that the required executable and libraries exist. `runc` typically requires elevated privileges and the appropriate Linux namespace/cgroup support.

## Benchmarks

Run the Python image benchmark only while the Python LazyFS mount is active and its paths match the constants in `bench.py`:

```bash
python bench.py
```

Run the Ubuntu benchmark only while the Ubuntu LazyFS mount is active:

```bash
python bench_ubuntu.py
```

Each script reads the ten largest unique regular-file blobs selected from its index and reports:

- Total indexed regular-file content.
- Number and size of selected files.
- Direct filesystem read time.
- Cold-cache LazyFS read time.
- Warm-cache LazyFS read time.

The benchmark removes only the selected digests from its cache before the cold-cache run. It then measures a cold read through the mount and a warm read with those files cached.

**Interpretation:** this benchmark measures reading a selected workload after the filesystem has already been prepared and mounted. It does not measure Docker image pull time, container startup time, registry-native lazy pulling, or end-to-end startup improvement. FUSE, HTTP, and hashing introduce overhead, so a cold read can be slower than a direct local read. Report the actual measurements; do not claim a speedup unless measurements support it.

## How each component works

### `indexer.py`

- Walks the supplied root filesystem without following directory symlinks.
- Records relative paths, file type, permissions/mode, and size.
- Records symlink targets.
- Computes SHA-256 for regular files in 1 MiB chunks.
- Stores each unique regular-file content blob at `STORE/blobs/<digest>`.
- Writes `STORE/index.json` via a temporary file and atomic replacement.
- Reports entry count, unique blob count, original bytes, unique bytes, and duplicate bytes.

Special files such as device nodes and sockets are marked `unsupported` by this prototype; they are not fully materialized.

### `blob_server.py`

- Serves `GET /blobs/<64-character-lowercase-sha256>`.
- Rejects malformed paths and invalid digests.
- Returns `404` for a missing blob.
- Optionally simulates response latency (`--latency`) and transfer bandwidth (`--bandwidth`).
- Logs served blobs.

Example with simulated network conditions:

```bash
python blob_server.py store --port 8000 --latency 100 --bandwidth 10
```

This simulates approximately 100 ms of latency per request and a transfer limit of 10 Mbps. It is an experiment setting, not a real network measurement.

### `lazyfs.py`

- Loads the complete local `index.json` at mount startup.
- Builds a directory-to-children lookup for directory listings.
- Resolves paths using index metadata.
- Defers content retrieval until a regular file's `read` operation.
- Fetches missing blobs from the HTTP server.
- Verifies the downloaded blob's SHA-256 before promoting it into the cache.
- Uses an atomic rename for the completed cache file.
- Exposes a read-only FUSE filesystem.

The prototype caches each complete file blob on first read; it does not currently fetch byte ranges from a file or resolve file contents across Docker image layers.

## Troubleshooting

### `ModuleNotFoundError: No module named 'fuse'`

Activate the virtual environment and install `fusepy`:

```bash
source .venv/bin/activate
python -m pip install fusepy
```

Also confirm the system FUSE library and `/dev/fuse` are available.

### `Transport endpoint is not connected` or a mount is stuck

Stop the LazyFS process, then try:

```bash
fusermount3 -u mnt
```

Use the actual mountpoint, such as `ubuntu-mnt` or `demo-mnt`. If the command is unavailable, try `fusermount -u <mountpoint>`. Do not remove the directory while it is still mounted.

### `Address already in use`

A server is already listening on that port. Reuse the existing server or select a different port and make sure the LazyFS `--server` URL uses the same port.

### `404 Blob not found`

The server's store must match the store used to build the index. For example, a LazyFS mount using `ubuntu-24.04-store` must connect to a server started with `ubuntu-24.04-store`.

### `No such file or directory`

Confirm the path exists in the index and that you are reading from the correct mountpoint. Image contents differ by tag and platform.

### Permission errors accessing the mount

Check `/etc/fuse.conf`, the `user_allow_other` setting, FUSE permissions, and whether the process was started with the appropriate privileges for your environment.

### Builder says output directory already exists

The builder intentionally refuses to overwrite generated rootfs/store directories. Reuse the existing store or safely rename/back up the old output before rebuilding. Avoid deleting data without checking what it contains.

## Limitations and next steps

Current implementation:

- Exports an image filesystem using Docker before indexing it.
- Stores one content-addressed blob per unique regular-file content.
- Fetches complete file blobs on first read from the project's HTTP server.
- Uses a local filesystem index, rather than a Docker Registry manifest and layer resolver.
- Has read-only FUSE behavior and limited support for special files.
- Has been demonstrated with an OCI `runc` bundle pointed at the mount.

Not implemented yet:

- Registry authentication and manifest/config resolution as part of the LazyFS workflow.
- Layer-aware path resolution, including overlay ordering and whiteout/deletion semantics.
- Efficient random access to individual files inside ordinary compressed tar layers.
- Native Docker/containerd integration through a snapshotter or equivalent runtime integration.
- Comprehensive handling of all filesystem metadata and special files.

A future registry-backed design would fetch manifest/config metadata, identify the platform-specific image and layers, map paths to the correct layer, retrieve content using a suitable indexable/random-access layer format or an explicit extraction strategy, verify content, cache it, and expose it through the filesystem to the container runtime.

The intended goal is to reduce the image data required before startup. That benefit must be measured after the registry and runtime integration exists; it should not be inferred from the current file-read benchmark.
