import logging
import re
from typing import Any

from agents.canvas_vision_agent import plan_visual_explanation
from agents.system_agent import launch_app
from result_queue import ResultQueue
from state import StateMachine

logger = logging.getLogger(__name__)

DETERMINISTIC_HINTS = {"system", "browser"}
LAUNCH_VERBS = ("open", "launch", "start", "run")


def classify(hint: str, task: str) -> str:
    if hint in DETERMINISTIC_HINTS:
        return "deterministic"
    return "model"


class AgentRunner:
    def __init__(self, result_queue: ResultQueue, state_machine: StateMachine) -> None:
        self.result_queue = result_queue
        self.state_machine = state_machine

    async def run(
        self,
        agent_id: str,
        call_id: str,
        task: str,
        context: str,
        hint: str,
    ) -> None:
        classification = classify(hint, task)
        try:
            if classification == "deterministic":
                result = self._run_deterministic(task, context, hint)
            elif hint == "canvas_vision":
                result = await self._run_canvas_vision(task, context)
            else:
                result = {
                    "success": False,
                    "message": "Model agents are not implemented in Phase 0.",
                }
        except Exception as exc:
            logger.exception("Agent failed")
            result = {
                "success": False,
                "message": str(exc),
            }
        finally:
            await self.state_machine.set_sub_agent_running(False, agent_id=agent_id)

        await self.result_queue.put(
            call_id,
            {
                "agent_id": agent_id,
                "classification": classification,
                "hint": hint,
                "result": result,
            },
        )

    async def _run_canvas_vision(self, task: str, context: str) -> dict[str, Any]:
        return await plan_visual_explanation(task, context)

    def _run_deterministic(
        self,
        task: str,
        context: str,
        hint: str,
    ) -> dict[str, Any]:
        if hint in {"system", "browser"}:
            app_name = self._app_name_from_task(task, context)
            return launch_app({"app_name": app_name})

        return {
            "success": False,
            "message": f"No deterministic handler for hint: {hint}.",
        }

    @staticmethod
    def _app_name_from_task(task: str, context: str) -> str:
        text = " ".join(part for part in (task, context) if part).strip()
        if not text:
            return ""

        lowered = text.lower()
        for verb in LAUNCH_VERBS:
            match = re.search(rf"\b{verb}\s+(.+?)(?:[.!?]|$)", lowered)
            if match:
                return match.group(1).strip()

        return text.strip()
