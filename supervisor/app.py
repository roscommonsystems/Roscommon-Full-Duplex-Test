from aiohttp import web


def _status_payload(registry, child):
    return {
        "current_repo": child.current_repo,
        "display_name": registry.display_name(child.current_repo) if child.current_repo else None,
        "state": child.state,
        "error": child.error,
    }


async def handle_models(request):
    return web.json_response(request.app["_registry"].models)


async def handle_status(request):
    registry = request.app["_registry"]
    child = request.app["_child"]
    return web.json_response(_status_payload(registry, child))


async def handle_select(request):
    registry = request.app["_registry"]
    child = request.app["_child"]
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    repo = body.get("repo")
    if not registry.has(repo):
        return web.json_response({"error": "unknown repo"}, status=400)
    if child.is_busy:
        return web.json_response({"error": "busy"}, status=409)
    if repo == child.current_repo and child.state == "ready":
        return web.json_response({"state": "ready"}, status=200)
    child.request_switch(repo)
    return web.json_response({"state": "loading"}, status=202)


def create_app(registry, child, static_dir=None):
    app = web.Application()
    app["_registry"] = registry
    app["_child"] = child
    app["_static_dir"] = static_dir
    app.router.add_get("/api/models", handle_models)
    app.router.add_get("/api/status", handle_status)
    app.router.add_post("/api/select", handle_select)
    return app
