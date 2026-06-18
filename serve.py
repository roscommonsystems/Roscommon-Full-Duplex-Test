#!/usr/bin/env python3
"""
serve.py — Launch the NVIDIA PersonaPlex (Moshi) full-duplex speech-to-speech server.

This is a thin Python entrypoint around NVIDIA's `moshi.server` module (which is
installed by provision.sh). It handles HF auth + temporary SSL certs and serves the
Web UI on 0.0.0.0:8998 by default.

Prerequisites (same as provision.sh):
  - moshi already installed (run provision.sh first, or `pip install ./personaplex/moshi`)
  - Accept the license: https://huggingface.co/nvidia/personaplex-7b-v1
  - A READ token:       https://huggingface.co/settings/tokens

Usage:
    export HF_TOKEN=hf_xxxxxxxx
    python serve.py                 # serves on 0.0.0.0:8998
    python serve.py --port 9000     # custom port
    python serve.py --cpu-offload   # if GPU VRAM is tight (needs `accelerate`)
"""
import argparse
import os
import subprocess
import sys
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve NVIDIA PersonaPlex (Moshi S2S).")
    parser.add_argument("--host", default="0.0.0.0",
                        help="bind address (default: 0.0.0.0, required for external access)")
    parser.add_argument("--port", type=int, default=8998,
                        help="port to serve on (default: 8998)")
    parser.add_argument("--cpu-offload", action="store_true",
                        help="offload model layers to CPU if GPU VRAM is insufficient "
                             "(requires the `accelerate` package)")
    args = parser.parse_args()

    if not os.environ.get("HF_TOKEN"):
        sys.exit(
            "ERROR: HF_TOKEN is not set.\n"
            "  1) Accept the license: https://huggingface.co/nvidia/personaplex-7b-v1\n"
            "  2) Create a READ token: https://huggingface.co/settings/tokens\n"
            "  3) export HF_TOKEN=hf_xxxxxxxx   (then re-run)"
        )

    # Default HF cache to the persistent workspace dir on vast.ai
    os.environ.setdefault("HF_HOME", "/workspace/.hf_home")

    # moshi.server needs a directory for its self-signed TLS cert
    ssl_dir = tempfile.mkdtemp(prefix="moshi-ssl-")

    cmd = [
        sys.executable, "-m", "moshi.server",
        "--host", args.host,
        "--port", str(args.port),
        "--ssl", ssl_dir,
    ]
    if args.cpu_offload:
        cmd.append("--cpu-offload")

    print(f"Launching: {' '.join(cmd)}")
    print("Once loaded, open the Web UI at https://<public-ip>:<mapped-port> "
          "(self-signed cert — click through the browser warning, then allow mic).")
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
