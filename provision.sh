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
# HOW TO USE — pick ONE:
#
#   A) Manually, after SSHing into the instance:
#        export HF_TOKEN=hf_xxxxxxxx
#        bash provision.sh
#
#   B) As a vast.ai On-start Script:
#        - In the instance config, add an env var:  HF_TOKEN=hf_xxxxxxxx
#        - Paste this whole file into the "On-start Script" box.
#        - Make sure port 8998 is exposed in the Docker options.
#
# IMPORTANT: rent a "datacenter:" offer (not a plain "host:" one). Many hobbyist
# "host:" machines have firewalled outbound internet and cannot download the model
# from HuggingFace. This script egress-tests before doing anything.
# =============================================================================
set -euo pipefail

PORT="${MOSHI_PORT:-8998}"
REPO_DIR="${REPO_DIR:-/workspace/personaplex}"
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

log "Installing system dependencies (opus, portaudio)"
apt-get update -qq || true
apt-get install -y -qq libopus-dev libportaudio2

log "Cloning PersonaPlex repository"
if [ ! -d "$REPO_DIR/.git" ]; then
  git clone --depth 1 https://github.com/NVIDIA/personaplex "$REPO_DIR"
else
  echo "Repo already present at $REPO_DIR — skipping clone."
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

log "Sanity check — torch sees the GPU"
python - <<'PY'
import torch
assert torch.cuda.is_available(), "CUDA not available!"
print("torch", torch.__version__, "| CUDA", torch.version.cuda,
      "| GPU capability", torch.cuda.get_device_capability())
PY

# --- Launch ----------------------------------------------------------------
log "Writing run script and launching server (tmux session 'moshi')"
cat > /workspace/run_moshi.sh <<EOF
#!/bin/bash
source $VENV/bin/activate
export HF_TOKEN=$HF_TOKEN
export HF_HOME=$HF_HOME
export SSL_DIR=\$(mktemp -d)
cd $REPO_DIR
exec python -m moshi.server --host 0.0.0.0 --port $PORT --ssl "\$SSL_DIR"
EOF
chmod +x /workspace/run_moshi.sh

tmux kill-session -t moshi 2>/dev/null || true
tmux new-session -d -s moshi 'bash /workspace/run_moshi.sh > /workspace/moshi.log 2>&1'

log "Waiting for model to load (first run downloads ~16GB; up to ~10 min)"
ready=0
for _ in $(seq 1 60); do
  if grep -q "Running on https://0.0.0.0:$PORT" /workspace/moshi.log 2>/dev/null; then
    ready=1; break
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
