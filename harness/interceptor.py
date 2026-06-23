import asyncio
import json
import logging
import time
import uuid
from typing import Any

from agent_runner import AgentRunner
from canvas_types import normalize_canvas_step
from result_queue import ResultQueue
from screen_context import capture_active_monitor
from state import StateMachine

logger = logging.getLogger(__name__)


class ToolInterceptor:
    def __init__(self, result_queue: ResultQueue, state_machine: StateMachine) -> None:
        self.result_queue = result_queue
        self.state_machine = state_machine
        self.agent_runner = AgentRunner(result_queue, state_machine)
        self._approval_lock = asyncio.Lock()

    async def handle_function_call(self, ws: Any, event: dict[str, Any]) -> str | None:
        name = self._get_tool_name(event)
        call_id = self._get_call_id(event)
        args = self._get_arguments(event)

        if not name or not call_id:
            logger.error("Invalid function call event: %s", event)
            return None

        logger.info("Handling RT2 tool call: %s", name)

        if name == "spawn_agent":
            await self._handle_spawn_agent(ws, call_id, args)
        elif name == "get_screen_context":
            try:
                result = capture_active_monitor()
            except Exception:
                logger.exception("Screen capture failed")
                result = {
                    "status": "error",
                    "message": "I couldn't capture the screen.",
                }
                await self._send_tool_result(ws, call_id, result)
                return
            await self._send_screen_context_result(ws, call_id, result)
        elif name == "draw_annotation":
            step = args.get("step")
            if not isinstance(step, dict):
                await self._send_tool_result(
                    ws,
                    call_id,
                    {
                        "status": "error",
                        "message": "draw_annotation requires a step object.",
                    },
                )
                return
            try:
                normalized_step = normalize_canvas_step(step)
            except Exception:
                logger.exception("Canvas annotation normalization failed")
                await self._send_tool_result(
                    ws,
                    call_id,
                    {
                        "status": "error",
                        "message": "Canvas annotation failed.",
                    },
                )
                return
            await self.state_machine.broadcast_canvas_event(
                {
                    "type": "draw_annotation",
                    "step": normalized_step,
                }
            )
            await self._send_tool_result(
                ws,
                call_id,
                {
                    "status": "ok",
                    "drawn": len(normalized_step["annotations"]),
                },
            )
            return "draw_annotation"
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
            await self._handle_approval_gate(ws, call_id, args)
        else:
            await self._send_tool_result(
                ws,
                call_id,
                {
                    "status": "error",
                    "message": f"Unknown tool: {name}",
                },
            )
        return name

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

    async def _send_screen_context_result(
        self,
        ws: Any,
        call_id: str,
        result: dict[str, Any],
    ) -> None:
        image = result.get("image")
        if not isinstance(image, dict):
            await self._send_tool_result(ws, call_id, result)
            return

        image_base64 = str(image.get("base64", ""))
        mime_type = str(image.get("mime_type", "image/jpeg"))
        tool_result = {
            "status": result.get("status"),
            "target": result.get("target"),
            "coordinate_space": result.get("coordinate_space"),
            "monitor": result.get("monitor"),
            "image": {
                "mime_type": mime_type,
                "width": image.get("width"),
                "height": image.get("height"),
            },
            "vision_context": "A screen image was attached as a conversation image immediately before this tool result.",
        }
        await self._send_tool_result(ws, call_id, tool_result)
        await ws.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_image",
                                "image_url": f"data:{mime_type};base64,{image_base64}",
                            }
                        ],
                    },
                }
            )
        )

    async def _handle_approval_gate(
        self,
        ws: Any,
        call_id: str,
        args: dict[str, Any],
    ) -> None:
        preview = str(args.get("preview", ""))
        action_type = str(args.get("action_type", ""))
        async with self._approval_lock:
            response = await self.state_machine.request_approval(
                call_id=call_id,
                preview=preview,
                action_type=action_type,
                timeout=60.0,
            )

        if response is None:
            await self._send_tool_result(
                ws,
                call_id,
                {
                    "approved": False,
                    "reason": "timeout",
                },
            )
            return

        await self._send_tool_result(
            ws,
            call_id,
            {
                "approved": bool(response["approved"]),
            },
        )

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
