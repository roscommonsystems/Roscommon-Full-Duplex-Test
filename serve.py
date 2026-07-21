#!/usr/bin/env python3
"""serve.py — Roscommon Full Duplex Test supervisor.

Owns the public port: serves the built client, exposes the model-select
control API, manages a moshi.server child, and reverse-proxies /api/chat
to it. Selecting a model in the UI restarts the child with a new --hf-repo.
"""
import asyncio
import functools
import glob
import os
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

# ============================== Configuration ==============================
# Edit in place.

HOST = "0.0.0.0"
PORT = 8998                  # public port — must be exposed on the instance
CHILD_PORT = 8999            # moshi.server, bound to localhost
DEFAULT_REPO = "nvidia/personaplex-7b-v1"   # model pre-loaded at boot
STATIC_DIR = os.path.join(ROOT, "client", "dist")
USE_SSL = True               # False serves plain HTTP (local testing only —
                             # the browser needs HTTPS to grant mic access)
CPU_OFFLOAD = False

ENABLE_ASR = True            # live "You:" transcription; costs ~2-6GB VRAM
ASR_PORT = 8997              # must stay in sync with PORT in asr_server.py
# ==========================================================================


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
    launcher = entry.get("server_python") or sys.executable
    cmd = [launcher, "-m", "moshi.server",
           "--host", "127.0.0.1", "--port", str(port), "--hf-repo", hf_repo]
    if entry.get("server_python"):
        # The forked moshi (pharma) warmup() calls torch.cuda.set_device(self.device);
        # torch 2.11 rejects a bare "cuda" device, so pin an explicit GPU index.
        cmd += ["--device", "cuda:0"]
    if cpu_offload:
        cmd.append("--cpu-offload")
    weight_file = entry.get("moshi_weight_file")
    if weight_file:
        weight_repo = entry.get("moshi_weight_repo", repo)
        path = await _download_weight(weight_repo, weight_file)
        cmd += ["--moshi-weight", path]
    # Voice prompts: a model may pin its own voice dir (e.g. pharma needs a .wav
    # reference voice — the finetuned model rejects the base .pt voices). Otherwise
    # models loaded on a base repo borrow the base model's voices.
    voice_dir = entry.get("voice_prompt_dir")
    if voice_dir:
        cmd += ["--voice-prompt-dir", voice_dir]
    elif base_repo or entry.get("needs_shared_voices"):
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
    if not os.environ.get("HF_TOKEN"):
        sys.exit(
            "ERROR: HF_TOKEN is not set.\n"
            "  1) Accept the license: https://huggingface.co/nvidia/personaplex-7b-v1\n"
            "  2) Create a READ token: https://huggingface.co/settings/tokens\n"
            "  3) export HF_TOKEN=hf_xxxxxxxx   (then re-run)"
        )
    os.environ.setdefault("HF_HOME", "/workspace/.hf_home")

    if not os.path.isdir(STATIC_DIR):
        print(f"WARNING: static dir not found ({STATIC_DIR}); "
              "build the client with `cd client && npm install && npm run build`.")

    registry = ModelRegistry.from_file(os.path.join(ROOT, "models.json"))
    if not registry.has(DEFAULT_REPO):
        sys.exit(f"ERROR: DEFAULT_REPO {DEFAULT_REPO} is not in models.json")

    from supervisor.scenarios import ScenarioStore
    scenarios_path = os.path.join(ROOT, "scenarios.json")
    scenarios = ScenarioStore.from_file(scenarios_path) if os.path.isfile(scenarios_path) else None

    builder = functools.partial(build_moshi_cmd, cpu_offload=CPU_OFFLOAD, registry=registry)
    child = ChildManager(builder, port=CHILD_PORT)

    asr = None
    if ENABLE_ASR:
        asr = AsrChild([sys.executable, os.path.join(ROOT, "asr_server.py")], port=ASR_PORT)

    app = create_app(registry, child, static_dir=STATIC_DIR, asr=asr, scenarios=scenarios)

    async def _boot(app):
        # Pre-load the default model before accepting conversations.
        await child.switch(DEFAULT_REPO)
        if child.state == "ready":
            print(f"Model ready: {registry.display_name(DEFAULT_REPO)}", flush=True)
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

    ssl_ctx = self_signed_ssl_context() if USE_SSL else None
    scheme = "https" if USE_SSL else "http"
    print(f"Pre-loading {registry.display_name(DEFAULT_REPO)} ({DEFAULT_REPO})...")
    print(f"Serving on {scheme}://<public-ip>:{PORT} "
          f"(self-signed cert — click through the warning, then allow mic).")
    web.run_app(app, host=HOST, port=PORT, ssl_context=ssl_ctx)


if __name__ == "__main__":
    main()
