import asyncio
import aiohttp


class ChildManager:
    """Owns the moshi.server child subprocess and its load state."""

    def __init__(self, command_builder, port=8999, ready_timeout=180.0):
        self._build = command_builder
        self.port = port
        self.ready_timeout = ready_timeout
        self.state = "loading"
        self.current_repo = None
        self.error = None
        self._proc = None
        self._lock = asyncio.Lock()
        self._session = None
        self._bg_tasks = set()

    async def _ensure_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _wait_ready(self):
        session = await self._ensure_session()
        url = f"ws://127.0.0.1:{self.port}/api/chat"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.ready_timeout
        while loop.time() < deadline:
            if self._proc and self._proc.returncode is not None:
                raise RuntimeError(f"child exited early (code {self._proc.returncode})")
            try:
                async with session.ws_connect(url, timeout=5) as ws:
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                        return
            except Exception:
                await asyncio.sleep(0.5)
        raise TimeoutError("child did not become ready in time")

    async def stop(self):
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=20)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None

    async def _start(self, repo):
        cmd = self._build(repo, self.port)
        self._proc = await asyncio.create_subprocess_exec(*cmd)
        self.current_repo = repo
        await self._wait_ready()

    async def switch(self, repo):
        async with self._lock:
            if repo == self.current_repo and self.state == "ready":
                return
            self.state = "loading"
            self.error = None
            try:
                await self.stop()
                await self._start(repo)
                self.state = "ready"
            except Exception as e:  # noqa: BLE001
                self.error = str(e)
                self.state = "error"
                await self.stop()

    @property
    def is_busy(self):
        return self.state == "loading"

    def request_switch(self, repo):
        self.state = "loading"
        task = asyncio.ensure_future(self.switch(repo))
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def aclose(self):
        for task in list(self._bg_tasks):
            task.cancel()
        if self._bg_tasks:
            await asyncio.gather(*self._bg_tasks, return_exceptions=True)
        await self.stop()
        if self._session and not self._session.closed:
            await self._session.close()
