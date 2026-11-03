
import argparse
import hashlib
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def make_handler(store_dir, bandwidth_mbps=0, latency_ms=0):
    store_dir = Path(store_dir).resolve()
    blobs_dir = store_dir / "blobs"

    class BlobHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            # Only allow requests for a SHA-256 blob.
            parts = self.path.split("?")[0].strip("/").split("/")

            if len(parts) != 2 or parts[0] != "blobs":
                self.send_error(404, "Not found")
                return

            digest = parts[1]

            if len(digest) != 64 or any(
                c not in "0123456789abcdef" for c in digest
            ):
                self.send_error(400, "Invalid SHA-256 hash")
                return

            blob_path = blobs_dir / digest

            if not blob_path.is_file():
                self.send_error(404, "Blob not found")
                return

            size = blob_path.stat().st_size

            # Simulate network latency and bandwidth for experiments.
            if latency_ms > 0:
                time.sleep(latency_ms / 1000)

            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()

            try:
                with open(blob_path, "rb") as blob:
                    while chunk := blob.read(64 * 1024):
                        self.wfile.write(chunk)

                        if bandwidth_mbps > 0:
                            seconds = (
                                len(chunk) * 8
                                / (bandwidth_mbps * 1_000_000)
                            )
                            time.sleep(seconds)

                print(
                    f"FETCH {digest} ({size:,} bytes)",
                    flush=True,
                )

            except (BrokenPipeError, ConnectionResetError):
                print(f"CLIENT DISCONNECTED: {digest}", flush=True)

        def log_message(self, fmt, *args):
            print(
                f"[HTTP] {self.client_address[0]} - {fmt % args}",
                flush=True,
            )

    return BlobHandler


def make_server(store_dir, host="127.0.0.1", port=8000,
                bandwidth_mbps=0, latency_ms=0):
    handler = make_handler(store_dir, bandwidth_mbps, latency_ms)
    return ThreadingHTTPServer((host, port), handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Serve content-addressed filesystem blobs over HTTP"
    )
    parser.add_argument("store", help="Path to the content-addressed store")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--bandwidth", type=float, default=0,
                        help="Approximate transfer limit in Mbps; 0 means unlimited")
    parser.add_argument("--latency", type=float, default=0,
                        help="Simulated latency in milliseconds")
    args = parser.parse_args()

    server = make_server(
        args.store,
        args.host,
        args.port,
        args.bandwidth,
        args.latency,
    )

    print(
        f"Serving blobs from {Path(args.store).resolve()}/blobs "
        f"at http://{args.host}:{args.port}",
        flush=True,
    )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping blob server")
    finally:
        server.server_close()
