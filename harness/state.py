import asyncio
import json
import logging
from enum import Enum
from typing import Any

from websockets.exceptions import ConnectionClosed
from websockets.legacy.server import WebSocketServerProtocol, serve

logger = logging.getLogger(__name__)


class HarnessState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    DICTATION = "DICTATION"


class StateMachine:
    def __init__(self) -> None:
        self.state = HarnessState.IDLE
        self.sub_agent_running = False
        self._clients: set[WebSocketServerProtocol] = set()
        self._server: Any | None = None
        self._lock = asyncio.Lock()

    async def start_ui_server(self, host: str = "127.0.0.1", port: int = 8766) -> None:
        if self._server is not None:
            return
        self._server = await serve(self._handle_ui_client, host, port)
        logger.info("UI WebSocket listening on ws://%s:%s", host, port)

    async def stop_ui_server(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        for client in list(self._clients):
            await client.close()
        self._clients.clear()

    async def transition_to(self, state: HarnessState) -> None:
        async with self._lock:
            self.state = state
            logger.info("Harness state: %s", state.value)
            await self._broadcast(
                {
                    "type": "state_change",
                    "state": state.value,
                }
            )

    async def set_sub_agent_running(
        self,
        running: bool,
        agent_id: str | None = None,
    ) -> None:
        async with self._lock:
            self.sub_agent_running = running
            await self._broadcast(
                {
                    "type": "agent_status",
                    "running": running,
                    "agent_id": agent_id,
                }
            )

    async def idle(self) -> None:
        await self.transition_to(HarnessState.IDLE)

    async def listening(self) -> None:
        await self.transition_to(HarnessState.LISTENING)

    async def thinking(self) -> None:
        await self.transition_to(HarnessState.THINKING)

    async def speaking(self) -> None:
        await self.transition_to(HarnessState.SPEAKING)

    async def dictation(self) -> None:
        await self.transition_to(HarnessState.DICTATION)

    async def _handle_ui_client(self, websocket: WebSocketServerProtocol) -> None:
        self._clients.add(websocket)
        try:
            await websocket.send(
                json.dumps(
                    {
                        "type": "state_change",
                        "state": self.state.value,
                    }
                )
            )
            await websocket.wait_closed()
        finally:
            self._clients.discard(websocket)

    async def _broadcast(self, message: dict[str, Any]) -> None:
        if not self._clients:
            return

        payload = json.dumps(message)
        disconnected: list[WebSocketServerProtocol] = []
        for client in self._clients:
            try:
                await client.send(payload)
            except ConnectionClosed:
                disconnected.append(client)

        for client in disconnected:
            self._clients.discard(client)
