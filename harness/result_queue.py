import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class QueuedResult:
    call_id: str
    data: dict[str, Any]


class ResultQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[QueuedResult] = asyncio.Queue()

    def empty(self) -> bool:
        return self._queue.empty()

    async def put(self, call_id: str, data: dict[str, Any]) -> None:
        await self._queue.put(QueuedResult(call_id=call_id, data=data))

    async def inject_next(self, ws: Any) -> bool:
        if self._queue.empty():
            return False

        result = await self._queue.get()
        logger.info("Injecting queued result for call_id=%s", result.call_id)
        await ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": result.call_id,
                        "output": json.dumps(result.data),
                    },
                }
            )
        )
        await ws.send(json.dumps({"type": "response.create"}))
        return True
