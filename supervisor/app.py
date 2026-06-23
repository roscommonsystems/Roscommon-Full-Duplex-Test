import os
import asyncio
import aiohttp
from aiohttp import web

_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host",
}


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


async def _proxy_ws(request):
    child = request.app["_child"]
    session = request.app["_client_session"]
    ws_server = web.WebSocketResponse()
    await ws_server.prepare(request)
    target = f"ws://127.0.0.1:{child.port}{request.rel_url}"
    try:
        async with session.ws_connect(target) as ws_client:
            async def pump(src, dst):
                async for msg in src:
                    if msg.type == aiohttp.WSMsgType.BINARY:
                        await dst.send_bytes(msg.data)
                    elif msg.type == aiohttp.WSMsgType.TEXT:
                        await dst.send_str(msg.data)
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSING,
                                      aiohttp.WSMsgType.ERROR):
                        break
                await dst.close()
            await asyncio.gather(pump(ws_server, ws_client), pump(ws_client, ws_server))
    finally:
        if not ws_server.closed:
            await ws_server.close()
    return ws_server


async def handle_proxy(request):
    child = request.app["_child"]
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return await _proxy_ws(request)
    session = request.app["_client_session"]
    target = f"http://127.0.0.1:{child.port}{request.rel_url}"
    data = await request.read()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    async with session.request(request.method, target, headers=headers,
                               data=data, allow_redirects=False) as resp:
        body = await resp.read()
        out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in _HOP_BY_HOP}
        return web.Response(status=resp.status, headers=out_headers, body=body)


async def handle_static(request):
    static_dir = request.app["_static_dir"]
    if not static_dir:
        return web.Response(status=404, text="no static dir")
    rel = request.match_info.get("tail", "")
    root = os.path.abspath(static_dir)
    candidate = os.path.normpath(os.path.join(root, rel))
    if (candidate == root or candidate.startswith(root + os.sep)) and os.path.isfile(candidate):
        return web.FileResponse(candidate)
    index = os.path.join(root, "index.html")
    if os.path.isfile(index):
        return web.FileResponse(index)
    return web.Response(status=404, text="not found")


async def _on_startup(app):
    app["_client_session"] = aiohttp.ClientSession()


async def _on_cleanup(app):
    sess = app.get("_client_session")
    if sess and not sess.closed:
        await sess.close()


def create_app(registry, child, static_dir=None):
    app = web.Application()
    app["_registry"] = registry
    app["_child"] = child
    app["_static_dir"] = os.path.abspath(static_dir) if static_dir else None
    app.router.add_get("/api/models", handle_models)
    app.router.add_get("/api/status", handle_status)
    app.router.add_post("/api/select", handle_select)
    app.router.add_route("*", "/api/{tail:.*}", handle_proxy)
    app.router.add_get("/{tail:.*}", handle_static)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app
