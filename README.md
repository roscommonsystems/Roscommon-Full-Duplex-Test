# Roscommon Full-Duplex Test

Reproducible setup for running **NVIDIA PersonaPlex** — a 7B real-time, full-duplex
speech-to-speech conversational model (built on Kyutai's Moshi architecture) — on a
[vast.ai](https://vast.ai) GPU instance.

The whole setup is automated by [`provision.sh`](./provision.sh).

---

## Prerequisites (one-time, on Hugging Face)

1. A Hugging Face account.
2. **Accept the model license:** https://huggingface.co/nvidia/personaplex-7b-v1
3. **Create a READ token:** https://huggingface.co/settings/tokens

> The token and license acceptance are per-account — each person needs their own.

## Renting the GPU

- **RTX 5090 (32 GB) is the default and recommended GPU.** The model uses ~19.5 GB and
  the live transcription (ASR) adds a few GB, so 32 GB is the practical minimum. A 24 GB
  RTX 4090 works only if you disable transcription.
- **Rent a `datacenter:` offer, not a plain `host:` one.** Many hobbyist `host:`
  machines have firewalled outbound internet and cannot download the model from
  Hugging Face. `provision.sh` egress-tests before doing anything and will tell you
  to swap hosts if it lands on a bad one.
- Image: **PyTorch (Vast)**, ~100 GB container disk.
- **Expose port `8998`** in the Docker options.

## Usage

### Option A — Manual (recommended for your first run)

You see the host-health check, download progress, and the final URL live, so you can
catch a bad host instantly.

```bash
# SSH into the instance (use the Connect button's command), then:
export HF_TOKEN=hf_xxxxxxxx
bash provision.sh
```

Copy the `https://<ip>:<port>` link it prints at the end.

### Option B — Hands-free (for repeat one-click spin-ups)

Once you trust the flow:

1. In the instance config, add an env var: `HF_TOKEN=hf_xxxxxxxx`
2. Paste the contents of `provision.sh` into vast.ai's **On-start Script** box.
3. Make sure port `8998` is exposed.

The instance boots with PersonaPlex already serving — no SSH needed. (Downside: if it
lands on a bad host it fails silently at boot, so prefer Option A until you're confident.)

## Accessing the Web UI

Open the printed `https://<ip>:<port>` link. The TLS cert is self-signed, so the
browser warns *"your connection is not private"* — click **Advanced → Proceed**, then
**Allow microphone**, pick a voice/role, and start talking. It's real-time: just speak
and it replies.

---

## How `provision.sh` works

1. Verifies `HF_TOKEN` is set.
2. **Egress-tests Hugging Face** (refuses to continue on a firewalled host).
3. Installs system deps (`libopus-dev`, `libportaudio2`).
4. Clones the [PersonaPlex repo](https://github.com/NVIDIA/personaplex).
5. Installs `moshi` with `--no-deps`, then adds its other deps — see note below.
6. Installs the app's own deps from `requirements.txt` (aiohttp, faster-whisper, …).
7. Sanity-checks that torch sees the GPU.
8. Launches the Moshi server in a `tmux` session on `0.0.0.0:8998`.
9. Waits for the model to load, then prints the public URL.

### Note: the Blackwell (RTX 5090) gotcha

`moshi` pins `torch>=2.2,<2.5` (a CUDA 12.1 build), which has **no kernels for
Blackwell GPUs** (RTX 5090, `sm_120`) and fails at runtime with *"no kernel image is
available."* The vast PyTorch image already ships a Blackwell-capable torch (cu128),
so the script installs `moshi` with `--no-deps` to preserve it, then installs the
remaining dependencies explicitly. (On Ada GPUs like the 4090 the default torch is
fine, and this is harmless.)

---

## Tool calling

PersonaPlex / Moshi has **no native tool/function calling** — it's pure speech-in →
speech-out. NVIDIA confirmed this in the
[model discussion](https://huggingface.co/nvidia/personaplex-7b-v1/discussions/2) and
say they may add it in future models.

There is an **experimental** workaround that hangs off the model's *Inner Monologue*
text channel: prompt the model to say a trigger phrase when it needs info, transcribe
the request (e.g. with NVIDIA Parakeet), query a tool/MCP server, then inject the
result back — either by restarting the model with the result in its text prompt, or by
drip-feeding text into the Inner Monologue channel while muting native audio and
playing TTS over it. This is a custom wrapper, not a built-in feature.

## Useful commands (on the instance)

```bash
tail -f /workspace/moshi.log     # server logs
tmux attach -t moshi             # attach to the server session
bash /workspace/run_moshi.sh     # restart the server only
```
