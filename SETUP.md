# Setup & Deploy Guide — Roscommon Full Duplex Test

How to stand up the PersonaPlex demo on a vast.ai GPU and open the web UI.
Anyone on the team should be able to follow this unaided.

---

## 1. One-time prerequisites

### Hugging Face (per person)
1. Create a Hugging Face account.
2. **Accept the model licenses** (open each, click agree):
   - https://huggingface.co/nvidia/personaplex-7b-v1 (base)
   - https://huggingface.co/kyutai/personaplex-rl-seamless (RL seamless)
   - https://huggingface.co/demegire/personaplex-finetune-pharma (pharma)
3. Create a **READ token**: https://huggingface.co/settings/tokens → this is your `HF_TOKEN`.

### vast.ai (per person)
1. Create a vast.ai account and **load credits**.
2. Add your **SSH public key**: Account → SSH Keys (so you can SSH into instances).
3. Create an **API key**: https://cloud.vast.ai/manage-keys/ → this is your `VAST_API_KEY`
   (only needed for the in-UI "Shut down instance" button; optional otherwise).

### GitHub
This repo is **private**, so the instance needs read access to clone it. Create a
**personal access token** with `repo` (read) scope → this is your `GITHUB_TOKEN`.

---

## 2. Rent the GPU

In the vast.ai console, pick an offer with:
- **GPU:** RTX 4090 (24 GB) or RTX 5090 (32 GB). The model uses ~19.5 GB VRAM.
- **A `datacenter:` offer** (not a plain `host:` one — many host machines have
  firewalled outbound internet and can't download the model; the script egress-tests
  and will tell you to swap if it lands on a bad one).
- **Image:** PyTorch (Vast) — e.g. `vastai/pytorch_cuda-13.2.1-auto/jupyter`.
- **Disk:** ~100 GB.
- **Expose port `8998`** in the Docker/launch options.

---

## 3. Deploy

SSH into the instance (use the Connect button's command), then:

```bash
export HF_TOKEN=hf_xxxxxxxx          # from step 1
export GITHUB_TOKEN=ghp_xxxxxxxx     # from step 1 (private repo)
export VAST_API_KEY=xxxxxxxx         # optional — enables the Shut down button

git clone https://$GITHUB_TOKEN@github.com/roscommonsystems/Roscommon-Full-Duplex-Test \
  /workspace/Roscommon-Full-Duplex-Test
cd /workspace/Roscommon-Full-Duplex-Test
bash provision.sh
```

`provision.sh` egress-tests the host, installs deps, clones the PersonaPlex model code,
installs Node and **builds the web client**, then launches the server. First boot
downloads ~16 GB (a few minutes). When you see **`Model ready:`** in the log, it's up.

> **Hands-free option:** set `HF_TOKEN`, `GITHUB_TOKEN`, `VAST_API_KEY` as instance
> env vars and paste the contents of `provision.sh` into vast.ai's **On-start Script**
> box (port 8998 exposed). The instance boots serving the demo with no SSH.

---

## 4. Open the demo

1. Find the instance's **public IP** and the **external port mapped to 8998**
   (vast.ai console shows the mapping).
2. Open `https://<ip>:<port>`.
3. The TLS cert is self-signed → the browser warns "your connection is not private" →
   **Advanced → Proceed**, then **Allow microphone**.

### Using it
- **Model dropdown** — pick PersonaPlex (Original), RL Seamless, or Pharma. Switching
  to a different model reloads it (~45 s; you'll see a "Loading…" screen).
- **Text Prompt / Voice** — set the persona and voice, then **Connect** and talk.
- **Shut down instance** (button at the bottom, only shown if `VAST_API_KEY` was set) —
  destroys the instance and stops billing when you're done. Use this after a demo.

---

## 5. Useful commands (on the instance)

```bash
tail -f /workspace/moshi.log     # server logs
tmux attach -t moshi             # attach to the server session
bash /workspace/run_moshi.sh     # restart the server only
```

## 6. Stopping / cleanup

- Easiest: click **Shut down instance** in the UI.
- Or destroy the instance from the vast.ai console (the ■/trash controls).
- Destroying stops all billing; "Stopping" keeps the disk (small storage charge).

---

## Notes

- One model is loaded in VRAM at a time; switching restarts the model server.
- The self-signed cert warning is expected; mic access needs the HTTPS (secure) context.
- `VAST_API_KEY` is your vast.ai **account** key (Bearer auth). If unset, the app runs
  fine — the Shut down button just doesn't appear.
