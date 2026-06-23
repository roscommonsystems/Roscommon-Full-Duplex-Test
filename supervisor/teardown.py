import os
import re

VAST_API_BASE = "https://console.vast.ai/api/v0"


def resolve_instance_id():
    """Return this container's vast.ai instance id, or None if not resolvable."""
    cid = os.environ.get("CONTAINER_ID")
    if cid and cid.strip():
        return cid.strip()
    label = os.environ.get("VAST_CONTAINERLABEL", "")
    m = re.search(r"\d+", label)
    if m:
        return m.group(0)
    override = os.environ.get("VAST_INSTANCE_ID")
    if override and override.strip():
        return override.strip()
    return None


def teardown_available():
    """True iff a vast API key is set AND we can resolve our own instance id."""
    return bool(os.environ.get("VAST_API_KEY")) and resolve_instance_id() is not None


async def destroy_self(session, api_key, instance_id, base=VAST_API_BASE):
    """DELETE this instance via the vast.ai API. Returns parsed JSON on 200,
    raises RuntimeError on any non-200 response."""
    url = f"{base}/instances/{instance_id}/"
    headers = {"Authorization": f"Bearer {api_key}"}
    async with session.delete(url, headers=headers) as resp:
        if resp.status != 200:
            body = await resp.text()
            raise RuntimeError(f"vast API {resp.status}: {body}")
        return await resp.json(content_type=None)
