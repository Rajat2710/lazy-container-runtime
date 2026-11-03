
import json
import time
from pathlib import Path

ROOTFS = Path("rootfs")
MOUNT = Path("mnt")
STORE = Path("store")
CACHE = Path("cache")
FILE_COUNT = 10


def read_file(path):
    total = 0
    with open(path, "rb") as file:
        while chunk := file.read(1024 * 1024):
            total += len(chunk)
    return total


def benchmark(label, base_dir, files):
    start = time.perf_counter()
    total_bytes = 0

    for path, _entry in files:
        total_bytes += read_file(base_dir / path.lstrip("/"))

    elapsed = time.perf_counter() - start
    print(f"{label}:")
    print(f"  Bytes read: {total_bytes:,}")
    print(f"  Time:       {elapsed:.4f} seconds")
    print()

    return elapsed


def main():
    index = json.loads((STORE / "index.json").read_text())["files"]

    regular_files = [
        (path, entry)
        for path, entry in index.items()
        if entry.get("type") == "file" and entry.get("size", 0) > 0
        and entry.get("sha256")
    ]

    total_image_bytes = sum(entry["size"] for _, entry in regular_files)

    # Select the largest files, avoiding duplicate content blobs.
    regular_files.sort(key=lambda item: item[1]["size"], reverse=True)
    selected = []
    seen_hashes = set()

    for path, entry in regular_files:
        digest = entry["sha256"]
        if digest not in seen_hashes:
            selected.append((path, entry))
            seen_hashes.add(digest)
        if len(selected) == FILE_COUNT:
            break

    if not selected:
        raise RuntimeError("No suitable files found in the index.")

    selected_bytes = sum(entry["size"] for _, entry in selected)
    selected_hashes = {entry["sha256"] for _, entry in selected}
    unique_blob_bytes = sum(
        next(entry["size"] for _, entry in selected
             if entry["sha256"] == digest)
        for digest in selected_hashes
    )

    print(f"Indexed regular-file content: {total_image_bytes:,} bytes")
    print(f"Selected workload: {len(selected)} files")
    print(f"Selected file content: {selected_bytes:,} bytes")
    print(f"Unique blob content to fetch: {unique_blob_bytes:,} bytes")
    print(f"Share of indexed content: {selected_bytes / total_image_bytes:.1%}")
    print("\nFiles selected:")
    for path, entry in selected:
        print(f"  {entry['size']:>10,} bytes  {path}")
    print()

    benchmark("1. Direct filesystem", ROOTFS, selected)

    # Remove only blobs used by this workload.
    for digest in selected_hashes:
        (CACHE / digest).unlink(missing_ok=True)

    print("Selected blobs removed from cache for cold run.\n")
    benchmark("2. Cold-cache LazyFS", MOUNT, selected)
    benchmark("3. Warm-cache LazyFS", MOUNT, selected)


if __name__ == "__main__":
    main()
