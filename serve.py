#!/usr/bin/env python3
"""serve.py — Roscommon Full Duplex Test supervisor.

Owns the public port: serves the built client, exposes the model-select
control API, manages a moshi.server child, and reverse-proxies /api/chat
to it. Selecting a model in the UI restarts the child with a new --hf-repo.
"""
import functools
import os
import ssl
import subprocess
import sys
import tempfile

from aiohttp import web

from supervisor.registry import ModelRegistry
from supervisor.child import ChildManager
from supervisor.asr import AsrChild
from supervisor.app import create_app, normalize_system_prompt, MIN_SYSTEM_PROMPT_CHARS

ROOT = os.path.dirname(os.path.abspath(__file__))

# ============================== Configuration ==============================
# Edit in place.

HOST = "0.0.0.0"
PORT = 8998                  # public port — must be exposed on the instance
CHILD_PORT = 8999            # moshi.server, bound to localhost
DEFAULT_REPO = "kyutai/personaplex-rl-seamless"   # model pre-loaded at boot
                             # (provision.sh has its own DEFAULT_REPO for the
                             # boot prefetch — change both together)
STATIC_DIR = os.path.join(ROOT, "client", "dist")
USE_SSL = True               # False serves plain HTTP (local testing only —
                             # the browser needs HTTPS to grant mic access)
CPU_OFFLOAD = False

ENABLE_ASR = True            # live "You:" transcription; costs ~2-6GB VRAM
ASR_PORT = 8997              # must stay in sync with PORT in asr_server.py
# ==========================================================================


def build_moshi_cmd(repo, port, cpu_offload=False):
    """Build the moshi.server argv for a model. Every model in models.json
    loads directly from its own HF repo."""
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
    if not os.environ.get("HF_TOKEN"):
        sys.exit(
            "ERROR: HF_TOKEN is not set.\n"
            "  1) Accept the license for each model in models.json:\n"
            "       https://huggingface.co/kyutai/personaplex-rl-seamless (default)\n"
            "       https://huggingface.co/nvidia/personaplex-7b-v1\n"
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

    builder = functools.partial(build_moshi_cmd, cpu_offload=CPU_OFFLOAD)
    child = ChildManager(builder, port=CHILD_PORT)

    asr = None
    if ENABLE_ASR:
        asr = AsrChild([sys.executable, os.path.join(ROOT, "asr_server.py")], port=ASR_PORT)

    # Optional deployment prompt. When set it becomes the prompt the UI starts
    # on, and is also offered as the "Customized" preset; when unset the client
    # falls back to its own built-in default. It comes from .env via spin_up.py,
    # which forwards it as a container env var. Both spellings are accepted
    # because Windows upper-cases environment keys and Linux does not, so a
    # lowercase `system_prompt=` in .env arrives as SYSTEM_PROMPT from one
    # laptop and system_prompt from another.
    raw_prompt = os.environ.get("SYSTEM_PROMPT") or os.environ.get("system_prompt")
    system_prompt = normalize_system_prompt(raw_prompt)
    if raw_prompt and not system_prompt:
        print(f"WARNING: SYSTEM_PROMPT is set but is not longer than "
              f"{MIN_SYSTEM_PROMPT_CHARS} characters — ignoring it.", flush=True)
    elif system_prompt:
        print(f'Custom system prompt loaded ({len(system_prompt)} chars) — '
              f'the UI will start on it, as the "Customized" preset.', flush=True)

    app = create_app(registry, child, static_dir=STATIC_DIR, asr=asr,
                     system_prompt=system_prompt)

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
