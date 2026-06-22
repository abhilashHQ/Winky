import asyncio
import json
import logging
import os
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from composio import Composio

from result_queue import ResultQueue

logger = logging.getLogger(__name__)

IDLE_TIMEOUT_SECONDS = 300.0
DEFAULT_COMPOSIO_USER_ID = "winky-user"
HINT_TOOLKITS: dict[str, list[str]] = {
    "email": ["gmail"],
    "slack": ["slack"],
    "calendar": ["googlecalendar"],
    "notion": ["notion"],
    "linear": ["linear"],
    "github": ["github"],
    "spotify": ["spotify"],
    "composio": ["gmail"],
}
COMPOSIO_HINTS = {
    "email",
    "calendar",
    "slack",
    "notion",
    "linear",
    "github",
    "spotify",
    "composio",
}


@dataclass(slots=True)
class MCPProcess:
    process: Any
    hint: str
    process_key: str
    last_call_id: str | None = None
    restart_count: int = 0
    monitor_task: asyncio.Task[None] | None = None
    request_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    initialized: bool = False
    tools_cache: list[dict[str, Any]] | None = None

    def is_alive(self) -> bool:
        if isinstance(self.process, subprocess.Popen):
            return self.process.poll() is None
        return self.process.returncode is None


def ensure_windows_subprocess_policy() -> None:
    if os.name != "nt":
        return
    policy_factory = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if policy_factory is None:
        return
    current_policy = asyncio.get_event_loop_policy()
    if not isinstance(current_policy, policy_factory):
        asyncio.set_event_loop_policy(policy_factory())


class MCPManager:
    def __init__(
        self,
        secrets: dict[str, str],
        result_queue: ResultQueue | None = None,
        idle_timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
    ) -> None:
        self._processes: dict[str, MCPProcess] = {}
        self._idle_timers: dict[str, asyncio.TimerHandle] = {}
        self._secrets = secrets
        self._result_queue = result_queue
        self._idle_timeout_seconds = idle_timeout_seconds
        self._shutting_down = False
        self._mcp_configs: dict[str, tuple[str, dict[str, str]]] = {}

    async def get_or_spawn(self, hint: str) -> MCPProcess:
        process_key = self._process_key_for_hint(hint)
        proc = self._processes.get(process_key)
        if proc is not None and proc.is_alive():
            self._reset_idle_timer(process_key)
            return proc

        proc = await self._spawn(process_key, hint=hint)
        self._processes[process_key] = proc
        self._reset_idle_timer(process_key)
        return proc

    async def list_tools(self, proc: MCPProcess) -> list[dict[str, Any]]:
        if proc.tools_cache is not None:
            return proc.tools_cache
        await self._initialize(proc)
        response = await self._send_request(proc, "tools/list", {})
        tools = response.get("result", {}).get("tools", [])
        if not isinstance(tools, list):
            raise RuntimeError(f"MCP {proc.hint} returned invalid tools/list response.")
        proc.tools_cache = [tool for tool in tools if isinstance(tool, dict)]
        return proc.tools_cache

    async def call_tool(
        self,
        proc: MCPProcess,
        tool_name: str,
        params: dict[str, Any],
        call_id: str | None = None,
    ) -> dict[str, Any]:
        proc.last_call_id = call_id
        await self._initialize(proc)
        return await self._send_request(
            proc,
            "tools/call",
            {"name": tool_name, "arguments": params},
        )

    async def shutdown(self) -> None:
        self._shutting_down = True
        for timer in self._idle_timers.values():
            timer.cancel()
        self._idle_timers.clear()

        for process_key in list(self._processes):
            await self._kill(process_key)

    async def _initialize(self, proc: MCPProcess) -> None:
        if proc.initialized:
            return
        response = await self._send_request(
            proc,
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {
                    "name": "winky-harness",
                    "version": "0.1.0",
                },
            },
        )
        if "error" in response:
            raise RuntimeError(f"MCP {proc.hint} initialize failed: {response['error']}")
        await self._send_notification(proc, "notifications/initialized", {})
        proc.initialized = True

    async def _send_request(
        self,
        proc: MCPProcess,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        if not proc.is_alive():
            raise RuntimeError(f"MCP {proc.hint} is not running.")
        if proc.process.stdin is None or proc.process.stdout is None:
            raise RuntimeError(f"MCP {proc.hint} stdio pipes are not available.")

        self._reset_idle_timer(proc.process_key)
        request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }

        async with proc.request_lock:
            if isinstance(proc.process, subprocess.Popen):
                response_line = await self._send_popen_request(proc.process, request)
            else:
                proc.process.stdin.write((json.dumps(request) + "\n").encode())
                await proc.process.stdin.drain()
                response_line = await proc.process.stdout.readline()

        if not response_line:
            raise RuntimeError(f"MCP {proc.hint} closed stdout before responding.")
        return json.loads(response_line)

    async def _send_notification(
        self,
        proc: MCPProcess,
        method: str,
        params: dict[str, Any],
    ) -> None:
        if proc.process.stdin is None:
            raise RuntimeError(f"MCP {proc.hint} stdin pipe is not available.")
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        async with proc.request_lock:
            if isinstance(proc.process, subprocess.Popen):
                await self._send_popen_notification(proc.process, notification)
            else:
                proc.process.stdin.write((json.dumps(notification) + "\n").encode())
                await proc.process.stdin.drain()

    async def _spawn(
        self,
        process_key: str,
        hint: str,
        restart_count: int = 0,
    ) -> MCPProcess:
        cmd, env = self._resolve(process_key)
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except NotImplementedError:
            process = await self._spawn_subprocess_thread(cmd, env)
        proc = MCPProcess(
            process=process,
            hint=hint,
            process_key=process_key,
            restart_count=restart_count,
        )
        proc.monitor_task = asyncio.create_task(self._monitor(process_key, proc))
        logger.info("Spawned MCP %s at %s", process_key, datetime.now(UTC).isoformat())
        return proc

    async def _spawn_subprocess_thread(self, cmd: list[str], env: dict[str, str]) -> subprocess.Popen[bytes]:
        return await asyncio.to_thread(
            subprocess.Popen,
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )

    async def _send_popen_request(self, process: subprocess.Popen[bytes], request: dict[str, Any]) -> bytes:
        return await asyncio.to_thread(self._send_popen_request_sync, process, request)

    async def _send_popen_notification(self, process: subprocess.Popen[bytes], notification: dict[str, Any]) -> None:
        await asyncio.to_thread(self._write_popen_message, process, notification)

    def _send_popen_request_sync(self, process: subprocess.Popen[bytes], request: dict[str, Any]) -> bytes:
        self._write_popen_message(process, request)
        if process.stdout is None:
            return b""
        return process.stdout.readline()

    @staticmethod
    def _write_popen_message(process: subprocess.Popen[bytes], message: dict[str, Any]) -> None:
        if process.stdin is None:
            return
        process.stdin.write((json.dumps(message) + "\n").encode())
        process.stdin.flush()

    def _resolve(self, process_key: str) -> tuple[list[str], dict[str, str]]:
        if process_key not in HINT_TOOLKITS:
            raise ValueError(f"Unsupported MCP process key: {process_key}")

        mcp_url, headers = self._get_composio_mcp_config(process_key)
        env = os.environ.copy()
        env["COMPOSIO_MCP_URL"] = mcp_url
        cmd = [
            self._node_runner_command(),
            "-y",
            "mcp-remote@latest",
            mcp_url,
            "--transport",
            "http-first",
            "--silent",
        ]
        for key, value in headers.items():
            cmd.extend(["--header", f"{key}:{value}"])
        return cmd, env

    def _get_composio_mcp_config(self, process_key: str) -> tuple[str, dict[str, str]]:
        if process_key in self._mcp_configs:
            return self._mcp_configs[process_key]

        api_key = self._secrets.get("composio_api_key")
        if not api_key:
            raise RuntimeError("Missing required Worker secret: composio_api_key")

        user_id = self._secrets.get("composio_user_id") or DEFAULT_COMPOSIO_USER_ID
        toolkits = HINT_TOOLKITS["composio"] if process_key == "composio" else HINT_TOOLKITS.get(process_key, ["gmail"])
        composio = Composio(api_key=api_key)
        session = composio.create(user_id=user_id, toolkits=toolkits)
        mcp_url = str(session.mcp.url)
        headers = dict(session.mcp.headers or {})
        self._mcp_configs[process_key] = (mcp_url, headers)
        logger.info("Created Composio Tool Router session for %s", ",".join(toolkits))
        return self._mcp_configs[process_key]

    @staticmethod
    def _node_runner_command() -> str:
        if os.name == "nt":
            return "npx.cmd"
        return "npx"

    @staticmethod
    def _process_key_for_hint(hint: str) -> str:
        if hint in COMPOSIO_HINTS:
            return hint
        return "composio"

    def _reset_idle_timer(self, process_key: str) -> None:
        if process_key in self._idle_timers:
            self._idle_timers[process_key].cancel()
        loop = asyncio.get_running_loop()
        self._idle_timers[process_key] = loop.call_later(
            self._idle_timeout_seconds,
            lambda: asyncio.create_task(self._kill(process_key)),
        )

    async def _kill(self, process_key: str) -> None:
        timer = self._idle_timers.pop(process_key, None)
        if timer is not None:
            timer.cancel()

        proc = self._processes.pop(process_key, None)
        if proc is None:
            return

        if proc.monitor_task is not None and proc.monitor_task is not asyncio.current_task():
            proc.monitor_task.cancel()

        if proc.process.returncode is None:
            proc.process.terminate()
            try:
                if isinstance(proc.process, subprocess.Popen):
                    await asyncio.wait_for(asyncio.to_thread(proc.process.wait), timeout=5.0)
                else:
                    await asyncio.wait_for(proc.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                proc.process.kill()
                if isinstance(proc.process, subprocess.Popen):
                    await asyncio.to_thread(proc.process.wait)
                else:
                    await proc.process.wait()

        logger.info("Killed MCP %s at %s", process_key, datetime.now(UTC).isoformat())

    async def _monitor(self, process_key: str, proc: MCPProcess) -> None:
        try:
            if isinstance(proc.process, subprocess.Popen):
                await asyncio.to_thread(proc.process.wait)
            else:
                await proc.process.wait()
        except asyncio.CancelledError:
            return

        if self._shutting_down:
            return
        if self._processes.get(process_key) is not proc:
            return
        if proc.process.returncode == 0:
            self._processes.pop(process_key, None)
            return

        if proc.restart_count >= 1:
            self._processes.pop(process_key, None)
            message = f"MCP {process_key} crashed twice (code {proc.process.returncode})."
            logger.error(message)
            if self._result_queue is not None and proc.last_call_id is not None:
                await self._result_queue.put(proc.last_call_id, {"error": message})
            return

        logger.warning(
            "MCP %s crashed (code %s), restarting once",
            process_key,
            proc.process.returncode,
        )
        try:
            new_proc = await self._spawn(
                process_key,
                hint=proc.hint,
                restart_count=proc.restart_count + 1,
            )
        except Exception as exc:
            self._processes.pop(process_key, None)
            message = f"MCP {process_key} failed to restart: {exc}"
            logger.exception(message)
            if self._result_queue is not None and proc.last_call_id is not None:
                await self._result_queue.put(proc.last_call_id, {"error": message})
            return

        new_proc.last_call_id = proc.last_call_id
        self._processes[process_key] = new_proc
        self._reset_idle_timer(process_key)
