#!/usr/bin/env python3
"""serve.py — Roscommon Full Duplex Test supervisor.

Owns the public port: serves the built client, exposes the model-select
control API, manages a moshi.server child, and reverse-proxies /api/chat
to it. Selecting a model in the UI restarts the child with a new --hf-repo.
"""
import argparse
import asyncio
import functools
import glob
import os
import shlex
import ssl
import subprocess
import sys
import tempfile

from aiohttp import web

from supervisor.registry import ModelRegistry
from supervisor.child import ChildManager
from supervisor.asr import AsrChild
from supervisor.app import create_app

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REPO = "nvidia/personaplex-7b-v1"


def shared_voices_dir():
    """Path to the base model's extracted voice prompts (downloaded when the
    base model loads). Fine-tunes that ship no voices.tgz of their own borrow
    these. Returns None if not present yet."""
    hf_home = os.environ.get("HF_HOME", "/workspace/.hf_home")
    pattern = os.path.join(
        hf_home, "hub", "models--nvidia--personaplex-7b-v1", "snapshots", "*", "voices"
    )
    matches = sorted(glob.glob(pattern))
    return matches[-1] if matches else None


async def _download_weight(weight_repo, weight_file):
    """Download a single weight file from HF (cached) without blocking the loop."""
    from huggingface_hub import hf_hub_download
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, functools.partial(hf_hub_download, weight_repo, weight_file)
    )


async def build_moshi_cmd(repo, port, cpu_offload=False, registry=None):
    """Build the moshi.server argv for a model. Most models load directly from
    their HF repo. Fine-tunes packaged as bare weights (e.g. pharma) declare a
    `base_repo` (for config/tokenizer/mimi/voices) and a `moshi_weight_file`
    (their checkpoint), which we download and pass via --moshi-weight."""
    entry = (registry.get(repo) if registry else None) or {}
    base_repo = entry.get("base_repo")
    hf_repo = base_repo or repo
    cmd = [sys.executable, "-m", "moshi.server",
           "--host", "127.0.0.1", "--port", str(port), "--hf-repo", hf_repo]
    if cpu_offload:
        cmd.append("--cpu-offload")
    weight_file = entry.get("moshi_weight_file")
    if weight_file:
        weight_repo = entry.get("moshi_weight_repo", repo)
        path = await _download_weight(weight_repo, weight_file)
        cmd += ["--moshi-weight", path]
    # Models loaded on a base repo (or flagged) have no voices.tgz of their own;
    # lend them the base model's voices.
    if base_repo or entry.get("needs_shared_voices"):
        vd = shared_voices_dir()
        if vd:
            cmd += ["--voice-prompt-dir", vd]
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
    p.add_argument("--asr-port", type=int, default=8997)
    p.add_argument("--no-asr", action="store_true", help="disable user transcription")
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

    builder = functools.partial(build_moshi_cmd, cpu_offload=args.cpu_offload, registry=registry)
    child = ChildManager(builder, port=args.child_port)

    asr = None
    asr_cmd = os.environ.get("ASR_CMD")
    if asr_cmd and not args.no_asr:
        asr = AsrChild(shlex.split(asr_cmd), port=args.asr_port)

    app = create_app(registry, child, static_dir=args.static, asr=asr)

    async def _boot(app):
        # Pre-load the default model before accepting conversations.
        await child.switch(args.hf_repo)
        if child.state == "ready":
            print(f"Model ready: {registry.display_name(args.hf_repo)}", flush=True)
        else:
            print(f"Model failed to load: {child.error}", flush=True)
        # Start the (persistent, model-agnostic) ASR child for user transcription.
        if asr is not None:
            await asr.start()
            print(f"ASR {'ready' if asr.available else 'unavailable: ' + str(asr.error)}",
                  flush=True)
    async def _shutdown(app):
        await child.aclose()
        if asr is not None:
            await asr.aclose()
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
