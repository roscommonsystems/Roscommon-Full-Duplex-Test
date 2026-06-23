#!/usr/bin/env python3
"""serve.py — Roscommon Full Duplex Test supervisor.

Owns the public port: serves the built client, exposes the model-select
control API, manages a moshi.server child, and reverse-proxies /api/chat
to it. Selecting a model in the UI restarts the child with a new --hf-repo.
"""
import argparse
import functools
import os
import ssl
import subprocess
import sys
import tempfile

from aiohttp import web

from supervisor.registry import ModelRegistry
from supervisor.child import ChildManager
from supervisor.app import create_app

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = "nvidia/personaplex-7b-v1"


def build_moshi_cmd(repo, port, cpu_offload=False):
    cmd = [sys.executable, "-m", "moshi.server",
           "--host", "127.0.0.1", "--port", str(port), "--hf-repo", repo]
    if cpu_offload:
        cmd.append("--cpu-offload")
    return cmd


def self_signed_ssl_context():
    """Generate a throwaway self-signed cert and return an SSLContext."""
    d = tempfile.mkdtemp(prefix="roscommon-ssl-")
    cert, key = os.path.join(d, "cert.pem"), os.path.join(d, "key.pem")
    subprocess.check_call([
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
        "-keyout", key, "-out", cert, "-days", "365",
        "-subj", "/CN=roscommon-full-duplex",
    ])
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    return ctx


def main():
    p = argparse.ArgumentParser(description="Serve Roscommon Full Duplex Test.")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8998)
    p.add_argument("--child-port", type=int, default=8999)
    p.add_argument("--hf-repo", default=DEFAULT_REPO, help="model to pre-load at boot")
    p.add_argument("--static", default=os.path.join(ROOT, "client", "dist"))
    p.add_argument("--cpu-offload", action="store_true")
    p.add_argument("--no-ssl", action="store_true", help="serve plain HTTP (local testing)")
    args = p.parse_args()

    if not os.environ.get("HF_TOKEN"):
        sys.exit(
            "ERROR: HF_TOKEN is not set.\n"
            "  1) Accept the license: https://huggingface.co/nvidia/personaplex-7b-v1\n"
            "  2) Create a READ token: https://huggingface.co/settings/tokens\n"
            "  3) export HF_TOKEN=hf_xxxxxxxx   (then re-run)"
        )
    os.environ.setdefault("HF_HOME", "/workspace/.hf_home")

    if not os.path.isdir(args.static):
        print(f"WARNING: static dir not found ({args.static}); "
              "build the client with `cd client && npm install && npm run build`.")

    registry = ModelRegistry.from_file(os.path.join(ROOT, "models.json"))
    if not registry.has(args.hf_repo):
        sys.exit(f"ERROR: --hf-repo {args.hf_repo} is not in models.json")

    builder = functools.partial(build_moshi_cmd, cpu_offload=args.cpu_offload)
    child = ChildManager(builder, port=args.child_port)
    app = create_app(registry, child, static_dir=args.static)

    async def _boot(app):
        # Pre-load the default model before accepting conversations.
        await child.switch(args.hf_repo)
    async def _shutdown(app):
        await child.aclose()
    app.on_startup.append(_boot)
    app.on_cleanup.append(_shutdown)

    ssl_ctx = None if args.no_ssl else self_signed_ssl_context()
    scheme = "http" if args.no_ssl else "https"
    print(f"Pre-loading {registry.display_name(args.hf_repo)} ({args.hf_repo})...")
    print(f"Serving on {scheme}://<public-ip>:{args.port} "
          f"(self-signed cert — click through the warning, then allow mic).")
    web.run_app(app, host=args.host, port=args.port, ssl_context=ssl_ctx)


if __name__ == "__main__":
    main()
