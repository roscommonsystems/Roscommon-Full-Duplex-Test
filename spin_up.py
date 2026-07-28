#!/usr/bin/env python3
"""spin_up.py — rent a vast.ai GPU and bring the demo up, in one command.

The zero-click counterpart to the manual flow in README.md (Option C). It:

  1. searches vast.ai for the cheapest offer matching the constants below,
  2. rents it with provision.sh as the on-start script and your tokens injected
     as container env vars,
  3. waits for the model to finish loading, then prints the URL.

Runs on your laptop, not on the GPU host — stdlib only, nothing to install.
Secrets come from .env (copy .env.example); everything else is a constant
below, edited in place.

    python spin_up.py
"""
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

# ============================ Configuration ================================
# Edit in place. Secrets belong in .env, never here.

# --- What to rent ---
GPU_NAME = "RTX_5090"          # vast's gpu_name; underscores, not spaces
NUM_GPUS = 1
MIN_GPU_RAM_GB = 32            # model ~19.5GB + a few GB for ASR
DISK_GB = 100                  # ~16GB of weights + CUDA wheels + client build
MAX_DOLLARS_PER_HOUR = 1.20    # walk away above this
MIN_RELIABILITY = 0.98         # vast's 0-1 host score
MIN_INET_DOWN_MBPS = 200       # the model download is ~16GB; slow hosts hurt
DATACENTER_ONLY = True         # plain host: offers often have firewalled egress

# --- How to configure it ---
# IMAGE must be a vast PyTorch image: provision.sh expects its /venv/main
# virtualenv and a Blackwell-capable torch. If the rental is rejected with an
# image error, copy the exact string from the console's image picker.
IMAGE = "vastai/pytorch:cuda-12.8.1-auto"
RUNTYPE = "ssh"                # keeps the console's Connect button working
LABEL = "roscommon-full-duplex"
MOSHI_PORT = 8998

# --- How long to wait ---
RUNNING_TIMEOUT_MIN = 15       # offer accepted -> container running
READY_TIMEOUT_MIN = 30         # container running -> model loaded
POLL_SECONDS = 15

# What to do when provisioning fails, times out, or you Ctrl-C: a rented
# instance bills whether or not the demo works, and the in-UI "Shut down"
# button is unreachable when the server never came up. True destroys it,
# False leaves it for debugging, "ask" prompts (and leaves it if you're
# not on a terminal).
DESTROY_ON_FAILURE = "ask"

# ==========================================================================

ROOT = os.path.dirname(os.path.abspath(__file__))
VAST_API_BASE = "https://console.vast.ai/api/v0"

# Passed into the container. VAST_API_KEY is what rents the instance in the
# first place, and rides along so the in-UI "Shut down instance" button works.
# The app repo is public, so nothing is needed to clone it.
REQUIRED_SECRETS = ("VAST_API_KEY", "HF_TOKEN")


def load_dotenv(path):
    """Read KEY=value lines from .env into the environment. Real environment
    variables win, so you can override a single value for one run. Tolerates
    `export ` prefixes so the README's deploy snippet can be pasted in verbatim."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def api(method, path, body=None, fatal=True):
    """Call the vast API and return parsed JSON, reporting the server's own
    message on failure (their errors are specific — don't bury them).

    fatal=False returns None instead of exiting, for calls made while cleaning
    up — a failed teardown must still print how to destroy the box by hand.
    """
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(VAST_API_BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {os.environ['VAST_API_KEY']}")
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        problem = (f"ERROR: vast API {method} {path} returned {e.code}\n"
                   + e.read().decode(errors="replace")[:800])
    except urllib.error.URLError as e:
        problem = f"ERROR: cannot reach the vast API ({e.reason})"
    if fatal:
        sys.exit(problem)
    print(problem)
    return None


def find_offers():
    """Cheapest-first offers matching the constants above."""
    # gpu_ram is megabytes, and cards report slightly under the round number
    # (a "32GB" 5090 reports ~32607MB), so scale by 1000 rather than 1024 —
    # *1024 would filter out the very card we're asking for.
    query = {
        "rentable": {"eq": True},
        "num_gpus": {"eq": NUM_GPUS},
        "gpu_ram": {"gte": MIN_GPU_RAM_GB * 1000},
        "disk_space": {"gte": DISK_GB},
        "dph_total": {"lte": MAX_DOLLARS_PER_HOUR},
        "reliability": {"gte": MIN_RELIABILITY},
        "inet_down": {"gte": MIN_INET_DOWN_MBPS},
        "order": [["dph_total", "asc"]],
        "limit": 20,
    }
    if GPU_NAME:
        query["gpu_name"] = {"eq": GPU_NAME}
    if DATACENTER_ONLY:
        query["datacenter"] = {"eq": True}
    return api("POST", "/bundles/", query).get("offers") or []


def rent(offer_id, onstart):
    """Rent an offer. Tokens ride in as docker -e flags; MOSHI_PORT is the
    only port we publish (vast maps it to a random external one)."""
    flags = [f"-p {MOSHI_PORT}:{MOSHI_PORT}"]
    flags += [f"-e {name}={os.environ[name]}" for name in REQUIRED_SECRETS
              if os.environ.get(name)]
    body = {
        "image": IMAGE,
        "disk": DISK_GB,
        "onstart": onstart,
        "env": " ".join(flags),
        "runtype": RUNTYPE,
        "label": LABEL,
    }
    result = api("PUT", f"/asks/{offer_id}/", body)
    if not result.get("success") or not result.get("new_contract"):
        sys.exit(f"ERROR: vast refused the rental — {result}")
    return result["new_contract"]


def get_instance(instance_id):
    for inst in api("GET", "/instances/").get("instances") or []:
        if str(inst.get("id")) == str(instance_id):
            return inst
    return None


def endpoint(inst):
    """(ip, external_port) for MOSHI_PORT, or (ip, None) if not mapped yet.
    vast returns docker's binding map: {"8998/tcp": [{"HostPort": "41234"}]}."""
    ip = (inst.get("public_ipaddr") or "").strip().rstrip("/")
    for binding in (inst.get("ports") or {}).get(f"{MOSHI_PORT}/tcp") or []:
        if binding.get("HostPort"):
            return ip, int(binding["HostPort"])
    return ip, None


def destroy(instance_id):
    """Destroy the instance, stopping billing."""
    if api("DELETE", f"/instances/{instance_id}/", fatal=False) is None:
        print(f"Could not destroy instance {instance_id} automatically. "
              "Destroy it by hand at https://cloud.vast.ai/instances/ — "
              "it is still billing.")
    else:
        print(f"Instance {instance_id} destroyed — billing stopped.")


def on_failure(instance_id, message):
    """Every path where the demo did not come up ends here. The instance is
    rented and billing regardless, and the in-UI Shut down button is no help
    when the server is what failed — so offer the exit from this side."""
    print(f"\n{message}")
    choice = DESTROY_ON_FAILURE
    if choice == "ask":
        try:
            reply = input(f"Destroy instance {instance_id} and stop billing? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            reply = ""  # not on a terminal — never destroy on a guess
        choice = reply.strip().lower().startswith("y")
    if choice:
        destroy(instance_id)
    else:
        print(f"\nInstance {instance_id} is still running AND BILLING.")
        print("  Destroy it at https://cloud.vast.ai/instances/ when you're done.")
        print("  Logs:  ssh into it, then tail -f /workspace/moshi.log")


def wait_for_running(instance_id):
    """Block until the container is running and its port is mapped. Returns
    (ip, port), or None if it never got there in time."""
    deadline = time.time() + RUNNING_TIMEOUT_MIN * 60
    last = None
    while time.time() < deadline:
        inst = get_instance(instance_id)
        if inst is None:
            sys.exit(f"ERROR: instance {instance_id} vanished — check the console.")
        status = inst.get("actual_status") or inst.get("cur_state") or "?"
        if status != last:
            print(f"  instance {instance_id}: {status}"
                  f"{' — ' + inst['status_msg'].strip() if inst.get('status_msg') else ''}")
            last = status
        ip, port = endpoint(inst)
        if status == "running" and ip and port:
            return ip, port
        time.sleep(POLL_SECONDS)
    return None


def wait_for_model(url):
    """Block until /api/status answers.

    serve.py loads the model in an on_startup hook, and aiohttp runs those
    before it binds the socket — so a refused connection means provision.sh is
    still working, and the first successful response means the load already
    resolved (one way or the other).
    """
    ctx = ssl.create_default_context()  # cert is self-signed by design
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    deadline = time.time() + READY_TIMEOUT_MIN * 60
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url + "/api/status")
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                return json.loads(resp.read().decode())
        except Exception:  # noqa: BLE001 — connection refused/reset while loading
            time.sleep(POLL_SECONDS)
    return None


def main():
    load_dotenv(os.path.join(ROOT, ".env"))
    missing = [n for n in REQUIRED_SECRETS if not os.environ.get(n)]
    if missing:
        sys.exit("ERROR: missing " + ", ".join(missing) + ".\n"
                 "Copy .env.example to .env and fill it in (see README.md step 1).")

    onstart_path = os.path.join(ROOT, "provision.sh")
    if not os.path.isfile(onstart_path):
        sys.exit(f"ERROR: {onstart_path} not found — run this from the repo.")
    with open(onstart_path, encoding="utf-8") as fh:
        # CRLF would break bash on the host; .gitattributes should prevent it,
        # but a stray checkout setting shouldn't cost you a rental.
        onstart = fh.read().replace("\r\n", "\n")

    print(f"Searching for {GPU_NAME or 'any GPU'} offers "
          f"(<= ${MAX_DOLLARS_PER_HOUR}/hr, {'datacenter only' if DATACENTER_ONLY else 'any host'})...")
    offers = find_offers()
    if not offers:
        sys.exit("ERROR: no offers matched. Loosen MAX_DOLLARS_PER_HOUR, "
                 "MIN_RELIABILITY or DATACENTER_ONLY at the top of this file.")

    for offer in offers[:5]:
        print(f"  ${offer.get('dph_total', 0):.3f}/hr  {offer.get('gpu_name')} "
              f"{int(offer.get('gpu_ram', 0)) // 1000}GB  "
              f"{offer.get('geolocation') or '?'}  "
              f"{int(offer.get('inet_down') or 0)}Mbps down  id={offer.get('id')}")

    pick = offers[0]
    print(f"\nRenting offer {pick['id']} at ${pick.get('dph_total', 0):.3f}/hr — "
          "billing starts now.")
    instance_id = rent(pick["id"], onstart)

    # From here on the instance is billing, so every exit goes through
    # on_failure() rather than just printing and leaving it running.
    try:
        print(f"\nWaiting for the container (up to {RUNNING_TIMEOUT_MIN} min)...")
        running = wait_for_running(instance_id)
        if running is None:
            return on_failure(instance_id, f"Container was not running after "
                                           f"{RUNNING_TIMEOUT_MIN} min.")
        ip, port = running
        url = f"https://{ip}:{port}"

        print(f"\nContainer up at {url}. Provisioning + first model download take "
              f"a while (up to {READY_TIMEOUT_MIN} min) — the port stays closed "
              "until the model has loaded.")
        status = wait_for_model(url)
    except KeyboardInterrupt:
        return on_failure(instance_id, "Interrupted — but the instance is rented "
                                       "and billing.")

    if status is None:
        return on_failure(instance_id,
                          f"Model not ready after {READY_TIMEOUT_MIN} min. It may "
                          "still be downloading — if so, destroying now throws away "
                          "the download.")
    if status.get("state") != "ready":
        return on_failure(instance_id, "Server is up but the model did not load: "
                                       f"{status.get('error') or status.get('state')}")

    print(f"\nUP — {status.get('display_name')} is loaded.")
    print(f"\n  Web UI:   {url}")
    print("            (self-signed cert: click through the warning, then allow mic)")
    print(f"  Instance: {instance_id}")
    print("  Shut down: the button in the UI, or https://cloud.vast.ai/instances/")


if __name__ == "__main__":
    main()
