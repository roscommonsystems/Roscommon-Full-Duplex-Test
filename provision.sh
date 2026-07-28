#!/usr/bin/env bash
# =============================================================================
# provision.sh  —  Stand up NVIDIA PersonaPlex (Moshi full-duplex speech-to-speech)
#                  on a vast.ai GPU instance, hands-free.
#
# Works on both Ada (RTX 4090) and Blackwell (RTX 5090) GPUs.
#
# PREREQUISITES (one-time, on huggingface.co):
#   1. Have a HuggingFace account.
#   2. Accept the model license at:
#        https://huggingface.co/nvidia/personaplex-7b-v1
#   3. Create a READ token at:
#        https://huggingface.co/settings/tokens
#
#   (optional) For the in-UI "Shut down instance" button, also set:
#        VAST_API_KEY=<your vast.ai account API key>   (https://cloud.vast.ai/manage-keys/)
#
# HOW THIS RUNS:
#
#   Normally you never invoke this yourself — spin_up.py passes it to vast.ai
#   as the instance's On-start Script, so it runs automatically at boot. (You
#   can also paste this whole file into the console's "On-start Script" box by
#   hand; set HF_TOKEN as an instance env var and expose port 8998.)
#
#   It is also safe to run directly on the instance when debugging, which is
#   the only way to watch it work in real time:
#        export HF_TOKEN=hf_xxxxxxxx
#        bash provision.sh
#   Re-running is idempotent — an already-cloned repo and installed deps are
#   detected and skipped.
#
# IMPORTANT: rent a "datacenter:" offer (not a plain "host:" one). Many hobbyist
# "host:" machines have firewalled outbound internet and cannot download the model
# from HuggingFace. This script egress-tests before doing anything.
# =============================================================================
set -euo pipefail

PORT="${MOSHI_PORT:-8998}"
REPO_DIR="${REPO_DIR:-/workspace/personaplex}"
APP_DIR="${APP_DIR:-/workspace/Roscommon-Full-Duplex-Test}"
APP_REPO="${APP_REPO:-github.com/roscommonsystems/Roscommon-Full-Duplex-Test}"
VENV="${VENV:-/venv/main}"
export HF_HOME="${HF_HOME:-/workspace/.hf_home}"

log(){ echo -e "\n=== $* ==="; }

# --- 0. Checks -------------------------------------------------------------
if [ -z "${HF_TOKEN:-}" ]; then
  echo "ERROR: HF_TOKEN is not set."
  echo "  1) Accept the license: https://huggingface.co/nvidia/personaplex-7b-v1"
  echo "  2) Make a READ token:  https://huggingface.co/settings/tokens"
  echo "  3) export HF_TOKEN=hf_xxxxxxxx   (then re-run)"
  exit 1
fi

log "Activating Python environment ($VENV)"
# shellcheck disable=SC1091
source "$VENV/bin/activate"

log "Egress check — can this host reach HuggingFace?"
code="$(curl -s -m 20 -o /dev/null -w '%{http_code}' https://huggingface.co || true)"
if [ "$code" != "200" ]; then
  echo "ERROR: cannot reach huggingface.co (HTTP '$code')."
  echo "This host has blocked/broken outbound internet. Destroy it and rent a"
  echo "'datacenter:' offer instead, then re-run."
  exit 1
fi
echo "HuggingFace reachable (HTTP 200)."

# --- Model prefetch, in the background --------------------------------------
# The ~16GB model download is the long pole of the whole boot, and nothing
# below needs it — so start it first and let apt/pip/npm run underneath it.
# hf_transfer multi-streams the download; the stock client is single-stream
# and rarely clears ~40MB/s, which alone is 6+ minutes of the wait.
# serve.py reads the same HF_HOME cache, so a finished prefetch means the
# server finds every file local; a failed one just means the server downloads
# whatever is missing itself, exactly as before.
log "Starting model download in the background (~16GB, overlaps the installs below)"
uv pip install -q 'huggingface-hub>=0.24,<0.25' hf_transfer
export HF_HUB_ENABLE_HF_TRANSFER=1
( huggingface-cli download nvidia/personaplex-7b-v1 \
    > /workspace/model_download.log 2>&1 ) &
MODEL_DL_PID=$!

log "Installing system dependencies (opus, portaudio, openssl)"
apt-get update -qq || true
apt-get install -y -qq libopus-dev libportaudio2 openssl

log "Cloning PersonaPlex repository"
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone --depth 1 https://github.com/NVIDIA/personaplex "$REPO_DIR"
else
  echo "Repo already present at $REPO_DIR — skipping clone."
fi

# --- Our app repo ----------------------------------------------------------
# Needed for serve.py, supervisor/, models.json and the client. The repo is
# public, so this clones with no credentials. When running this script manually
# you've usually already cloned it (then this is a no-op).
log "Fetching the Roscommon app repo ($APP_DIR)"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --depth 1 "https://${APP_REPO}" "$APP_DIR"
else
  echo "App repo already present at $APP_DIR — pulling latest."
  git -C "$APP_DIR" pull --ff-only || echo "(pull skipped — local changes or detached HEAD)"
fi

# --- Blackwell-safe install ------------------------------------------------
# moshi pins torch>=2.2,<2.5 (a CUDA 12.1 build) which has NO kernels for
# Blackwell GPUs (RTX 5090, sm_120) and fails at runtime with
# "no kernel image is available". The vast PyTorch image already ships a
# Blackwell-capable torch (cu128). So we install moshi WITHOUT its deps to
# preserve that torch, then add the remaining deps explicitly.
log "Installing moshi (--no-deps to preserve GPU-compatible torch)"
uv pip install "$REPO_DIR/moshi" --no-deps

log "Installing moshi's other dependencies"
uv pip install \
  'numpy>=1.26,<2.2' \
  'safetensors>=0.4.0,<0.5' \
  'huggingface-hub>=0.24,<0.25' \
  'einops==0.7' \
  'sentencepiece==0.2' \
  'sounddevice==0.5' \
  'sphn>=0.1.4,<0.2' \
  'aiohttp>=3.10.5,<3.11'

# Our own runtime deps (aiohttp, huggingface-hub, numpy, faster-whisper,
# onnxruntime). requirements.txt repeats the pins from the block above, so
# resolving faster-whisper here can't drag huggingface-hub past the version
# moshi tolerates. The overlapping packages are already satisfied — no-ops.
log "Installing the app's runtime dependencies (requirements.txt)"
uv pip install -r "$APP_DIR/requirements.txt"

log "Installing CUDA libs for faster-whisper's GPU path (isolated from moshi's torch)"
uv pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

log "Installing Node.js (to build the web client)"
if ! command -v node >/dev/null 2>&1; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y -qq nodejs
fi
echo "node $(node --version) / npm $(npm --version)"

log "Building the web client ($APP_DIR/client)"
( cd "$APP_DIR/client" && npm install && npm run build )

log "Sanity check — torch sees the GPU"
python - <<'PY'
import torch
assert torch.cuda.is_available(), "CUDA not available!"
print("torch", torch.__version__, "| CUDA", torch.version.cuda,
      "| GPU capability", torch.cuda.get_device_capability())
PY

log "Waiting for the background model download to finish"
if wait "$MODEL_DL_PID"; then
  echo "Model cache is warm."
else
  echo "Prefetch exited nonzero — the server will fetch whatever is missing itself."
  tail -n 5 /workspace/model_download.log || true
fi

# --- Prefetch the other models from models.json (background, non-gating) ----
# The UI can switch models, and a switch re-launches moshi.server on the new
# repo — blocking until its weights are local. Warming them now makes that
# switch near-instant. Started only *after* the default model finished, so the
# two never split the pipe while the boot is gated on the default; and fully
# detached, so provisioning neither waits on them nor fails with them (e.g. a
# model whose HF license hasn't been accepted just logs and stays cold).
EXTRA_MODELS="$(python - "$APP_DIR/models.json" <<'PY' || true
import json, sys
for m in json.load(open(sys.argv[1])):
    if m["id"] != "nvidia/personaplex-7b-v1":   # serve.py's DEFAULT_REPO
        print(m["id"])
PY
)"
for repo in $EXTRA_MODELS; do
  log "Prefetching $repo in the background (does not block startup)"
  nohup huggingface-cli download "$repo" \
      >> /workspace/model_prefetch_extra.log 2>&1 &
done

# --- Launch ----------------------------------------------------------------
log "Writing run script and launching server (tmux session 'moshi')"
cat > /workspace/run_moshi.sh <<EOF
#!/bin/bash
source $VENV/bin/activate
export HF_TOKEN=$HF_TOKEN
export HF_HOME=$HF_HOME
# Multi-stream HF downloads (installed above) — matters again when switching
# to a model that isn't cached yet, e.g. the RL fine-tune from models.json.
export HF_HUB_ENABLE_HF_TRANSFER=1
export VAST_API_KEY=${VAST_API_KEY:-}
export CONTAINER_ID=${CONTAINER_ID:-}
export VAST_CONTAINERLABEL=${VAST_CONTAINERLABEL:-}
# serve.py launches asr_server.py itself; both are configured by the constants
# at the top of those files (ENABLE_ASR / ASR_PORT and PORT / MODEL).
# Let CTranslate2 (faster-whisper) find the pip-installed CUDA libs for GPU.
PYSITE=\$(python -c "import site;print(site.getsitepackages()[0])" 2>/dev/null)
export LD_LIBRARY_PATH="\$PYSITE/nvidia/cublas/lib:\$PYSITE/nvidia/cudnn/lib:\${LD_LIBRARY_PATH:-}"
cd /workspace/Roscommon-Full-Duplex-Test
exec python serve.py
EOF
chmod +x /workspace/run_moshi.sh

tmux kill-session -t moshi 2>/dev/null || true
tmux new-session -d -s moshi 'bash /workspace/run_moshi.sh > /workspace/moshi.log 2>&1'

log "Waiting for model to load (cache is warm — this is mostly the load into VRAM)"
ready=0
for _ in $(seq 1 90); do
  if grep -q "Model ready:" /workspace/moshi.log 2>/dev/null; then
    ready=1; break
  fi
  if grep -q "Model failed to load" /workspace/moshi.log 2>/dev/null; then
    echo "ERROR: model failed to load — see /workspace/moshi.log"
    break
  fi
  sleep 10
done

# --- Report ----------------------------------------------------------------
PUB_IP="${PUBLIC_IPADDR:-$(curl -s -m 10 https://api.ipify.org || echo UNKNOWN)}"
EXT_PORT="$(printenv "VAST_TCP_PORT_$PORT" || echo "$PORT")"

if [ "$ready" = "1" ]; then
  log "PersonaPlex is UP"
else
  log "Server not confirmed ready yet — check the log (it may still be loading)"
fi
echo "Web UI:     https://$PUB_IP:$EXT_PORT"
echo "            (self-signed cert: click through the browser warning, then allow mic)"
echo "Logs:       tail -f /workspace/moshi.log"
echo "Attach:     tmux attach -t moshi"
echo "Restart:    bash /workspace/run_moshi.sh   (or re-run this script)"
