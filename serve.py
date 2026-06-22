#!/usr/bin/env python3
"""
serve.py — Launch the Roscommon Full Duplex Test server (NVIDIA PersonaPlex / Moshi).

Thin Python entrypoint around NVIDIA's `moshi.server`. It:
  - picks which model to load (--hf-repo),
  - writes the served UI's config.json with a friendly model name (read from
    models.json) so the frontend can display which model is loaded,
  - serves our custom-built client (client/dist) via --static,
  - starts the full-duplex speech-to-speech server with temporary SSL certs.

Prerequisites:
  - moshi installed (run provision.sh first).
  - client built: `cd client && npm install && npm run build`.
  - Accept the model license + set HF_TOKEN:
      https://huggingface.co/nvidia/personaplex-7b-v1
      https://huggingface.co/settings/tokens

Usage:
    export HF_TOKEN=hf_xxxxxxxx
    python serve.py                                   # base model
    python serve.py --hf-repo kyutai/personaplex-rl-seamless
    python serve.py --hf-repo demegire/personaplex-finetune-pharma
    python serve.py --port 9000 --cpu-offload
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = "nvidia/personaplex-7b-v1"


def model_display_name(hf_repo: str) -> str:
    """Map an HF repo id to a friendly name using models.json (fallback: the id)."""
    try:
        with open(os.path.join(ROOT, "models.json"), encoding="utf-8") as f:
            for m in json.load(f):
                if m.get("id") == hf_repo:
                    return m.get("name", hf_repo)
    except (OSError, ValueError):
        pass
    return hf_repo


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve Roscommon Full Duplex Test.")
    parser.add_argument("--host", default="0.0.0.0",
                        help="bind address (default: 0.0.0.0, required for external access)")
    parser.add_argument("--port", type=int, default=8998, help="port (default: 8998)")
    parser.add_argument("--hf-repo", default=DEFAULT_REPO,
                        help=f"HF repo of the model to load (default: {DEFAULT_REPO})")
    parser.add_argument("--static", default=os.path.join(ROOT, "client", "dist"),
                        help="directory of the built client to serve (default: client/dist)")
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

    os.environ.setdefault("HF_HOME", "/workspace/.hf_home")

    # Tell the UI which model is loaded (frontend reads ./config.json).
    if os.path.isdir(args.static):
        name = model_display_name(args.hf_repo)
        with open(os.path.join(args.static, "config.json"), "w", encoding="utf-8") as f:
            json.dump({"modelName": name, "hfRepo": args.hf_repo}, f)
        print(f"Serving model: {name}  ({args.hf_repo})")
    else:
        print(f"WARNING: static dir not found ({args.static}); "
              "build the client with `cd client && npm install && npm run build`.")

    ssl_dir = tempfile.mkdtemp(prefix="moshi-ssl-")
    cmd = [
        sys.executable, "-m", "moshi.server",
        "--host", args.host,
        "--port", str(args.port),
        "--hf-repo", args.hf_repo,
        "--static", args.static,
        "--ssl", ssl_dir,
    ]
    if args.cpu_offload:
        cmd.append("--cpu-offload")

    print(f"Launching: {' '.join(cmd)}")
    print("Once loaded, open https://<public-ip>:<mapped-port> "
          "(self-signed cert — click through the warning, then allow mic).")
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
