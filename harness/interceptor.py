import asyncio
import json
import logging
import time
import uuid
from typing import Any

from agent_runner import AgentRunner
from result_queue import ResultQueue
from state import StateMachine

logger = logging.getLogger(__name__)


class ToolInterceptor:
    def __init__(self, result_queue: ResultQueue, state_machine: StateMachine) -> None:
        self.result_queue = result_queue
        self.state_machine = state_machine
        self.agent_runner = AgentRunner(result_queue, state_machine)

    async def handle_function_call(self, ws: Any, event: dict[str, Any]) -> None:
        name = self._get_tool_name(event)
        call_id = self._get_call_id(event)
        args = self._get_arguments(event)

        if not name or not call_id:
            logger.error("Invalid function call event: %s", event)
            return

        if name == "spawn_agent":
            await self._handle_spawn_agent(ws, call_id, args)
        elif name == "get_screen_context":
            await self._send_tool_result(
                ws,
                call_id,
                {
                    "status": "mock",
                    "message": "Screen context is not implemented yet.",
                },
            )
        elif name == "draw_annotation":
            await self._send_tool_result(
                ws,
                call_id,
                {
                    "status": "mock",
                    "message": "Canvas annotations are not implemented yet.",
                    "step": args.get("step"),
                },
            )
        elif name == "start_dictation":
            await self.state_machine.dictation()
            await self._send_tool_result(
                ws,
                call_id,
                {
                    "status": "mock",
                    "message": "Dictation is not implemented yet.",
                    "context": args.get("context"),
                },
            )
        elif name == "approval_gate":
            await self._send_tool_result(
                ws,
                call_id,
                {
                    "status": "mock",
                    "approved": True,
                    "message": "Approval gate is stubbed for Phase 0.",
                    "action_type": args.get("action_type"),
                    "preview": args.get("preview"),
                },
            )
        else:
            await self._send_tool_result(
                ws,
                call_id,
                {
                    "status": "error",
                    "message": f"Unknown tool: {name}",
                },
            )

    async def _handle_spawn_agent(
        self,
        ws: Any,
        call_id: str,
        args: dict[str, Any],
    ) -> None:
        started_at = time.perf_counter()
        agent_id = str(uuid.uuid4())
        await self.state_machine.set_sub_agent_running(True, agent_id=agent_id)
        asyncio.create_task(
            self.agent_runner.run(
                agent_id=agent_id,
                call_id=call_id,
                task=str(args.get("task", "")),
                context=str(args.get("context", "")),
                hint=str(args.get("hint", "")),
            )
        )
        await self._send_tool_result(
            ws,
            call_id,
            {
                "status": "spawned",
                "agent_id": agent_id,
            },
        )
        elapsed_ms = (time.perf_counter() - started_at) * 1000
        if elapsed_ms > 50:
            logger.warning("spawn_agent ACK took %.2fms", elapsed_ms)
        else:
            logger.debug("spawn_agent ACK took %.2fms", elapsed_ms)

    async def _send_tool_result(
        self,
        ws: Any,
        call_id: str,
        data: dict[str, Any],
    ) -> None:
        await ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(data),
                    },
                }
            )
        )

    @staticmethod
    def _get_tool_name(event: dict[str, Any]) -> str | None:
        item = event.get("item")
        if isinstance(item, dict):
            return item.get("name")
        return event.get("name")

    @staticmethod
    def _get_call_id(event: dict[str, Any]) -> str | None:
        item = event.get("item")
        if isinstance(item, dict):
            return item.get("call_id") or item.get("id")
        return event.get("call_id") or event.get("id")

    @staticmethod
    def _get_arguments(event: dict[str, Any]) -> dict[str, Any]:
        item = event.get("item")
        raw_args = None
        if isinstance(item, dict):
            raw_args = item.get("arguments")
        raw_args = raw_args if raw_args is not None else event.get("arguments")

        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str) and raw_args:
            try:
                parsed = json.loads(raw_args)
            except json.JSONDecodeError:
                logger.warning("Failed to parse tool arguments: %s", raw_args)
                return {}
            if isinstance(parsed, dict):
                return parsed
        return {}
