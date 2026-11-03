
#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:-python:3.11-slim}"

# Keep the existing paths for the current Python image.
if [[ "$IMAGE" == "python:3.11-slim" ]]; then
    ROOTFS="$PWD/rootfs"
    STORE="$PWD/store"
else
    # Derive a safe directory suffix from the image name.
    SUFFIX=$(printf '%s' "$IMAGE" | sed 's/[^a-zA-Z0-9._-]/-/g')
    ROOTFS="$PWD/${SUFFIX}-rootfs"
    STORE="$PWD/${SUFFIX}-store"
fi

echo "Preparing image: $IMAGE"
echo "Root filesystem: $ROOTFS"
echo "Store: $STORE"

# Refuse to overwrite existing output directories.
if [[ -e "$ROOTFS" || -e "$STORE" ]]; then
    echo "Output directory already exists. Refusing to overwrite it."
    echo "Choose another image tag or move the existing output aside."
    exit 1
fi

mkdir -p "$ROOTFS"

echo "Pulling Docker image..."
docker pull "$IMAGE"

echo "Creating temporary container..."
CID=$(docker create "$IMAGE")

cleanup() {
    if [[ -n "${CID:-}" ]]; then
        docker rm -f "$CID" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

echo "Exporting container filesystem..."
docker export "$CID" | tar -x -C "$ROOTFS"

echo "Removing temporary container..."
docker rm "$CID" >/dev/null
CID=""
trap - EXIT

echo "Building content-addressed store..."
python indexer.py "$ROOTFS" "$STORE"

echo "Preparation complete."
