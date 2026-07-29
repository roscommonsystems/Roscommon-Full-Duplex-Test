#!/usr/bin/env python3
"""spin_up.py — rent a vast.ai GPU and bring the demo up, in one command.

The supported way to deploy the demo (see README.md section 2). It:

  1. searches vast.ai for the cheapest offer matching the constants below,
  2. rents it with provision.sh as the on-start script and your tokens injected
     as container env vars,
  3. waits for the model to finish loading, then prints the URL.

Runs on your laptop, not on the GPU host — stdlib only, nothing to install.
Secrets come from .env (copy .env.example); everything else is a constant
below, edited in place.

    python spin_up.py
"""
import base64
import gzip
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
GPU_NAME = "RTX 5090"          # vast's gpu_name; "RTX 5090" or "RTX_5090" both work
NUM_GPUS = 1
MIN_GPU_RAM_GB = 32            # model ~19.5GB + a few GB for ASR
DISK_GB = 100                  # ~16GB of weights + CUDA wheels + client build
MAX_DOLLARS_PER_HOUR = 1.50    # walk away above this
MIN_RELIABILITY = 0.90         # vast's 0-1 host score
MIN_INET_DOWN_MBPS = 500       # the model download is ~16GB; slow hosts hurt
DATACENTER_ONLY = True         # plain host: offers often have firewalled egress

# Hosts to never rent from, by machine_id. vast keeps listing machines whose
# docker daemon cannot actually hand a container a GPU, and since we always
# take the cheapest match, one broken machine gets picked every single run.
# The script prints the id to add here when a host fails to start.
#   100695 — "failed to inject CDI devices ... /gpu=0: unknown" (2026-07-28)
BLOCKED_MACHINE_IDS = (100695,)

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
HEARTBEAT_SECONDS = 60         # print *something* at least this often while waiting

# --- How long to keep it ---
# Wall-clock budget for the rental, in hours (24 = a day; 0.5 works too), or
# None to run open-endedly until you shut it down yourself.
#
# vast has no server-side "rent for N hours" — a contract bills until someone
# destroys it — so the deadline is armed *inside* the container by provision.sh
# and holds after this script exits and after you close the laptop. It also
# covers the failure paths, which is the point: a box that never finished
# provisioning bills exactly the same as one running the demo, and that's the
# one nobody remembers to go and destroy.
#
# What it does not survive: losing the instance's disk, or the container being
# stopped rather than running (a stopped instance has nothing to run the timer,
# though it also bills only for storage).
MAX_RUNTIME_HOURS = 12

# What to do when provisioning fails, times out, or you Ctrl-C: a rented
# instance bills whether or not the demo works, and the in-UI "Shut down"
# button is unreachable when the server never came up. True destroys it,
# False leaves it for debugging, "ask" prompts (and leaves it if you're
# not on a terminal).
DESTROY_ON_FAILURE = "ask"

# ==========================================================================

ROOT = os.path.dirname(os.path.abspath(__file__))
# The API version lives in each path below rather than here: vast is retiring
# v0 an endpoint at a time, so when the first call moves it changes on its own
# instead of dragging a shared prefix across the ones that haven't. Everything
# we call is still v0.
VAST_API_BASE = "https://console.vast.ai/api"

# vast's limits on the rental body. It enforces all three in one check and
# refuses with a 400 that names them together — "len(image) > 1024, or
# len(args) > 16384, or len(label) > 256" — without saying which one you tripped,
# and only *after* the search has run. check_limits() gets there first.
# ("args" is the on-start script.)
VAST_MAX_IMAGE_CHARS = 1024
VAST_MAX_ONSTART_CHARS = 16384
VAST_MAX_LABEL_CHARS = 256

# Where the on-start wrapper unpacks provision.sh on the instance. /workspace is
# the persistent volume, alongside the logs provision.sh writes.
ONSTART_DIR = "/workspace"
ONSTART_PATH = f"{ONSTART_DIR}/provision.sh"

# Passed into the container. VAST_API_KEY is what rents the instance in the
# first place, and rides along so the in-UI "Shut down instance" button works.
# The app repo is public, so nothing is needed to clone it.
REQUIRED_SECRETS = ("VAST_API_KEY", "HF_TOKEN")

# Forwarded to the container when .env sets it, never required: the UI starts
# on it, and offers it as the "Customized" prompt preset. Both spellings are
# read because Windows upper-cases environment keys and Linux does not, so a
# lowercase `system_prompt=` in .env survives either laptop. It always arrives
# on the instance as SYSTEM_PROMPT.
SYSTEM_PROMPT_KEYS = ("SYSTEM_PROMPT", "system_prompt")


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


def system_prompt():
    """The prompt from .env under either spelling, stripped, or None."""
    for key in SYSTEM_PROMPT_KEYS:
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return None


def pack_onstart(script):
    """provision.sh wrapped as a self-extracting on-start script.

    provision.sh outgrew vast's 16KB on-start limit in July 2026, and trimming
    it back under would only buy until the next paragraph of comments — it is a
    documented script and meant to stay one. So we don't send it as text: it
    goes gzipped, inside five lines of bash that unpack it on the host. Shell
    compresses about 4:1, which turns the cap from something a comment can walk
    into back into something you'd have to double the file to reach.

    The instance runs the byte-for-byte contents of your local provision.sh —
    including uncommitted edits, unlike fetching it from the repo — and the
    unpacked copy stays at ONSTART_PATH to read or re-run over ssh. base64 and
    gunzip are the only things this needs on the host; both are in the image.
    """
    # mtime=0 so an unchanged provision.sh packs to an identical payload rather
    # than a fresh one every run — the difference is then real, not a timestamp.
    blob = base64.encodebytes(gzip.compress(script.encode("utf-8"), 9, mtime=0))
    return (
        "#!/bin/bash\n"
        "# provision.sh, gzipped by spin_up.py to fit vast's on-start size cap.\n"
        f"# It unpacks below to {ONSTART_PATH} — read it, or re-run it, there.\n"
        "set -eo pipefail\n"
        f"mkdir -p {ONSTART_DIR}\n"
        # Quoted heredoc: no expansion, so the payload can't be mangled by the
        # shell. base64's alphabet cannot produce the terminator line.
        f"base64 -d <<'PROVISION_SH_B64' | gunzip > {ONSTART_PATH}\n"
        f"{blob.decode('ascii')}"
        "PROVISION_SH_B64\n"
        f"exec bash {ONSTART_PATH}\n"
    )


def check_limits(onstart, script):
    """Fail on anything vast would refuse the rental for, before we search.

    Everything here is a constant at the top of this file (or a file it reads),
    so it is ours to get wrong — and getting it wrong the other way costs a
    round trip through the search and a 400 that doesn't say which limit it was.
    """
    packed_note = (
        f"provision.sh is {len(script)} chars and packs to {len(onstart)}.\n"
        "  Nothing in this script can shrink that further — trim provision.sh, "
        "or host it\n  and fetch it from a one-line on-start script instead."
    )
    for what, value, limit, fix in (
        ("IMAGE", IMAGE, VAST_MAX_IMAGE_CHARS,
         "Copy a shorter image name from the console's image picker."),
        ("LABEL", LABEL, VAST_MAX_LABEL_CHARS,
         "Shorten LABEL at the top of this file."),
        ("the packed on-start script", onstart, VAST_MAX_ONSTART_CHARS,
         packed_note),
    ):
        if len(value) > limit:
            sys.exit(f"ERROR: {what} is {len(value)} characters; vast allows "
                     f"{limit}.\n  {fix}")


def api(method, path, body=None, fatal=True):
    """Call the vast API and return parsed JSON, reporting the server's own
    message on failure (their errors are specific — don't bury them).

    fatal=False is for calls that must not kill the script (teardown, calls the
    caller can answer with a retry). When the server sent a JSON error — vast's
    all carry success:false and a msg — that dict is returned so the caller can
    read *which* error; anything less structured prints and returns None.
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
        raw = e.read().decode(errors="replace")
        problem = f"ERROR: vast API {method} {path} returned {e.code}\n" + raw[:800]
        if not fatal:
            try:
                err = json.loads(raw)
                if isinstance(err, dict):
                    return err
            except ValueError:
                pass
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
        # The vast CLI writes gpu_name with underscores and swaps them for
        # spaces before it calls the API; the HTTP API itself does not, and
        # matches "RTX 5090" exactly — an underscore silently returns nothing.
        query["gpu_name"] = {"eq": GPU_NAME.replace("_", " ")}
    if DATACENTER_ONLY:
        query["datacenter"] = {"eq": True}
    offers = api("POST", "/v0/bundles/", query).get("offers") or []
    # Filtered here rather than in the query: several offers can share one
    # machine, so excluding by machine_id is what actually keeps a known-bad
    # host from coming back under a different offer id.
    offers = [o for o in offers if o.get("machine_id") not in BLOCKED_MACHINE_IDS]
    # Nearly every matching offer sits at the same bottom price, so the
    # tiebreak matters: take the fattest pipe among equally-cheap hosts — the
    # image pull and the model download both ride on it. Sorted here because
    # the API ignores a second order key (verified: it accepts the syntax but
    # returns price-sorted offers with inet_down unordered).
    offers.sort(key=lambda o: (o.get("dph_total") or 9e9,
                               -(o.get("inet_down") or 0)))
    return offers


def rent(offer_id, onstart):
    """Rent an offer; the new contract id, or None if the offer was already
    gone (rent the next one). Tokens ride in as container env vars; MOSHI_PORT
    is the only port we publish (vast maps it to a random external one).

    `env` is a dict, in the shape vast's own CLI produces from the docker-style
    flag string its docs show: `-e NAME=value` becomes a plain NAME->value
    entry, while `-p host:container` is kept whole as the key with a dummy
    value. Sending the flag string itself is rejected with "env must be a dict".
    """
    env = {f"-p {MOSHI_PORT}:{MOSHI_PORT}": "1"}
    env.update({name: os.environ[name] for name in REQUIRED_SECRETS
                if os.environ.get(name)})
    # provision.sh arms the self-destruct from this; unset means "no limit",
    # which is also what it means when you paste provision.sh in by hand.
    if MAX_RUNTIME_HOURS:
        env["MAX_RUNTIME_HOURS"] = str(MAX_RUNTIME_HOURS)
    prompt = system_prompt()
    if prompt:
        env["SYSTEM_PROMPT"] = prompt
    body = {
        "image": IMAGE,
        "disk": DISK_GB,
        "onstart": onstart,
        "env": env,
        "runtype": RUNTYPE,
        "label": LABEL,
    }
    result = api("PUT", f"/v0/asks/{offer_id}/", body, fatal=False) or {}
    if result.get("success") and result.get("new_contract"):
        return result["new_contract"]
    # The cheapest offers are contested and their ids rotate, so the id we
    # searched up is routinely gone seconds later. That's the one refusal the
    # caller can fix by itself — everything else (bad image, no credit) would
    # fail identically on every offer, so trying more of them just hides it.
    if "no_such_ask" in (result.get("msg") or ""):
        return None
    sys.exit(f"ERROR: vast refused the rental — {result or 'no response'}")


def get_instance(instance_id):
    """The instance's record, or None if vast no longer has it.

    Fetching one instance is still v0 — it was only the *collection* listing
    that moved to /v1, and that one is paginated, so scanning it for our own id
    would be the long way round. Despite the plural key, the payload here is a
    single object, not a list.
    """
    return api("GET", f"/v0/instances/{instance_id}/").get("instances") or None


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
    result = api("DELETE", f"/v0/instances/{instance_id}/", fatal=False)
    if result is None or not result.get("success"):
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
        if MAX_RUNTIME_HOURS:
            # provision.sh arms the timer before its own first check, so this
            # holds for everything except a container that never started.
            print(f"  Backstop: it destroys itself {MAX_RUNTIME_HOURS}h after the "
                  "container started, unless it never got as far as running "
                  "provision.sh.")
        print("  Destroy it at https://cloud.vast.ai/instances/ when you're done.")
        print("  Logs:  ssh into it, then tail -f /workspace/moshi.log")


class HostStartupError(RuntimeError):
    """The host's docker daemon refused to start the container. Nothing on our
    side can recover from it, and waiting out the timeout only bills for it."""


def _dur(seconds):
    """m:ss for a duration."""
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def _elapsed(since):
    """m:ss since `since`, for prefixing progress lines."""
    return _dur(time.time() - since)


def wait_for_running(instance_id):
    """Block until the container is running and its port is mapped. Returns
    (ip, port), or None if it never got there in time. Raises HostStartupError
    if the host reports a failure that will not resolve on its own."""
    start = time.time()
    deadline = start + RUNNING_TIMEOUT_MIN * 60
    last_shown, last_print = None, 0.0
    while time.time() < deadline:
        inst = get_instance(instance_id)
        if inst is None:
            sys.exit(f"ERROR: instance {instance_id} vanished — check the console.")
        status = inst.get("actual_status") or inst.get("cur_state") or "?"
        message = (inst.get("status_msg") or "").strip()
        headline = message.splitlines()[0] if message else ""
        # Print on any change of status OR message, not just status: the
        # status sits on "loading" for minutes while status_msg walks through
        # docker's layer-by-layer pull progress — showing that walk is what
        # proves it isn't hung. Failing both, heartbeat once a minute.
        if (status, headline) != last_shown:
            print(f"  [{_elapsed(start)}] instance {instance_id}: {status}"
                  f"{' — ' + headline if headline else ''}")
            last_shown, last_print = (status, headline), time.time()
        elif time.time() - last_print >= HEARTBEAT_SECONDS:
            print(f"  [{_elapsed(start)}] still {status} — waiting "
                  f"(gives up at {RUNNING_TIMEOUT_MIN}:00)")
            last_print = time.time()
        # A daemon error here is the host telling us the container will never
        # start (bad GPU/CDI wiring, usually). The status itself is no help —
        # it sits on "created", which is also a normal transient state — so the
        # message is what distinguishes a dead box from a slow one.
        if "Error response from daemon" in message:
            raise HostStartupError(
                f"host {inst.get('machine_id')} could not start the container.\n"
                f"  {message.splitlines()[0]}\n"
                f"  This is the host's fault, not the demo's — re-run to get a "
                f"different one, and add {inst.get('machine_id')} to "
                f"BLOCKED_MACHINE_IDS at the top of this file to stop picking it.")
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
    start = time.time()
    deadline = start + READY_TIMEOUT_MIN * 60
    # The port stays closed until the model is loaded, so there is no signal to
    # relay from this side — just prove we're alive and what we're waiting on.
    last_print = time.time()
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url + "/api/status")
            with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
                return json.loads(resp.read().decode())
        except Exception:  # noqa: BLE001 — connection refused/reset while loading
            if time.time() - last_print >= HEARTBEAT_SECONDS:
                print(f"  [{_elapsed(start)}] port still closed — provision.sh is "
                      f"installing deps and downloading the ~16GB model "
                      f"(normal; gives up at {READY_TIMEOUT_MIN}:00)")
                last_print = time.time()
            time.sleep(POLL_SECONDS)
    return None


def main():
    load_dotenv(os.path.join(ROOT, ".env"))
    missing = [n for n in REQUIRED_SECRETS if not os.environ.get(n)]
    if missing:
        sys.exit("ERROR: missing " + ", ".join(missing) + ".\n"
                 "Copy .env.example to .env and fill it in (see README.md step 1).")

    # Said out loud before the money is spent: a prompt that silently didn't
    # make it is only discovered once the demo is up and the wrong prompt is
    # loaded. 8 is the server's own threshold (MIN_SYSTEM_PROMPT_CHARS in
    # supervisor/app.py); this script stays stdlib-only, so it can't import it.
    prompt = system_prompt()
    if prompt and len(prompt) > 8:
        print(f'Forwarding SYSTEM_PROMPT from .env ({len(prompt)} chars) — '
              'the UI will start on it, as the "Customized" preset.')
    elif prompt:
        print(f'WARNING: SYSTEM_PROMPT in .env is only {len(prompt)} characters. '
              'The server ignores anything that short — the demo will open on '
              'the built-in default prompt instead.')

    onstart_path = os.path.join(ROOT, "provision.sh")
    if not os.path.isfile(onstart_path):
        sys.exit(f"ERROR: {onstart_path} not found — run this from the repo.")
    with open(onstart_path, encoding="utf-8") as fh:
        # CRLF would break bash on the host; .gitattributes should prevent it,
        # but a stray checkout setting shouldn't cost you a rental.
        script = fh.read().replace("\r\n", "\n")
    onstart = pack_onstart(script)
    check_limits(onstart, script)
    print(f"On-start script: provision.sh, {len(script)} chars packed to "
          f"{len(onstart)} (vast allows {VAST_MAX_ONSTART_CHARS}).")

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

    # Between the search and the rental the cheapest ids routinely vanish —
    # they're the ones everyone else's script is also grabbing, and vast
    # rotates them besides. A failed attempt costs nothing (money only moves
    # when a rental succeeds, and at most one does), so walk the list, and
    # refresh it once if the whole batch went stale under us.
    instance_id = None
    for attempt in (1, 2):
        for offer in offers:
            print(f"\nRenting offer {offer['id']} at "
                  f"${offer.get('dph_total', 0):.3f}/hr...")
            instance_id = rent(offer["id"], onstart)
            if instance_id:
                break
            print("  gone — someone else took it. Trying the next offer.")
            time.sleep(1)  # their API rate-limits bursts
        if instance_id or attempt == 2:
            break
        print("\nThe whole batch went stale — searching again.")
        offers = find_offers()
    if not instance_id:
        sys.exit("ERROR: every offer vanished before we could rent it — the "
                 "market is moving fast right now. Re-run in a minute, or raise "
                 "MAX_DOLLARS_PER_HOUR to bid on less contested offers.")
    print(f"Rented — instance {instance_id} is billing from now on.")
    # Boot splits into two halves and only this side can see the first one:
    # everything up to "running" is the host pulling the image, and provision.sh
    # does not exist yet to time it. The second half it times itself, per step.
    rented_at = time.time()

    # From here on the instance is billing, so every exit goes through
    # on_failure() rather than just printing and leaving it running.
    try:
        print(f"\nWaiting for the container (up to {RUNNING_TIMEOUT_MIN} min)...")
        running = wait_for_running(instance_id)
        if running is None:
            return on_failure(instance_id, f"Container was not running after "
                                           f"{RUNNING_TIMEOUT_MIN} min.")
        ip, port = running
        running_at = time.time()
        url = f"https://{ip}:{port}"

        print(f"\nContainer up at {url}. Provisioning + first model download take "
              f"a while (up to {READY_TIMEOUT_MIN} min) — the port stays closed "
              "until the model has loaded.")
        status = wait_for_model(url)
        ready_at = time.time()
    except KeyboardInterrupt:
        return on_failure(instance_id, "Interrupted — but the instance is rented "
                                       "and billing.")
    except (SystemExit, Exception) as e:  # noqa: BLE001 — see below
        # api() reports fatal errors by exiting, and wait_for_running() calls it
        # on every poll. Without this, a hiccup anywhere in the wait leaves a
        # rented box billing with nothing on screen offering to destroy it —
        # the one failure worse than the demo not coming up. Nothing raised
        # from here is worth more than the prompt, so catch the lot.
        return on_failure(instance_id, f"Provisioning failed: {e}")

    if status is None:
        return on_failure(instance_id,
                          f"Model not ready after {READY_TIMEOUT_MIN} min. It may "
                          "still be downloading — if so, destroying now throws away "
                          "the download.")
    if status.get("state") != "ready":
        return on_failure(instance_id, "Server is up but the model did not load: "
                                       f"{status.get('error') or status.get('state')}")

    print(f"\nUP — {status.get('display_name')} is loaded.")
    print(f"\n  Boot:     {_dur(ready_at - rented_at)} total — "
          f"{_dur(running_at - rented_at)} image pull, "
          f"{_dur(ready_at - running_at)} provisioning")
    print("            (per-step breakdown: /workspace/boot_timing.log on the instance)")
    print(f"\n  Web UI:   {url}")
    print("            (self-signed cert: click through the warning, then allow mic)")
    print(f"  Instance: {instance_id}")
    if MAX_RUNTIME_HOURS:
        # The watchdog's clock starts when the container did, which is roughly
        # when we saw it running — hence "~". The exact second is on the
        # instance, in /workspace/self_destruct_deadline.
        deadline = time.localtime(running_at + MAX_RUNTIME_HOURS * 3600)
        print(f"  Expires:  ~{time.strftime('%a %d %b %H:%M', deadline)} "
              f"({MAX_RUNTIME_HOURS}h budget) — destroys itself then, billing ends")
    print("  Shut down: the button in the UI, or https://cloud.vast.ai/instances/")


if __name__ == "__main__":
    main()
