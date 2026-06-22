import json
import logging
import re
from typing import Any

from agents.calendar_agent import book_meeting, find_free_slots, get_schedule, reschedule_meeting
from agents.canvas_vision_agent import plan_visual_explanation
from agents.email_agent import check_access, draft_and_send, reply_to_thread, summarize_thread, triage_inbox
from agents.slack_agent import read_messages, send_message
from agents.system_agent import launch_app
from mcp_manager import MCPManager
from result_queue import ResultQueue
from state import StateMachine
from urgency import learn_preference

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
        self.mcp_manager: MCPManager | None = None

    def set_mcp_manager(self, mcp_manager: MCPManager) -> None:
        self.mcp_manager = mcp_manager

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
            elif hint == "email":
                result = await self._run_email(task, context)
            elif hint == "slack":
                result = await self._run_slack(task, context)
            elif hint == "calendar":
                result = await self._run_calendar(task, context)
            elif hint == "canvas_vision":
                result = await self._run_canvas_vision(task, context)
            else:
                result = {
                    "success": False,
                    "message": "Model agents are not implemented for this hint yet.",
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

    async def _run_email(self, task: str, context: str) -> dict[str, Any]:
        if self.mcp_manager is None:
            raise RuntimeError("MCP manager is not initialized.")

        text = " ".join(part for part in (task, context) if part).strip()
        lowered = text.lower()
        if lowered.startswith("learn urgency preference:"):
            correction = text.split(":", 1)[1].strip()
            return learn_preference(correction)
        if "learn urgency preference:" in lowered:
            correction = text[lowered.index("learn urgency preference:") + len("learn urgency preference:") :].strip()
            return learn_preference(correction)
        if self._is_email_access_check(lowered):
            return await check_access(self.mcp_manager)
        if "draft_and_send" in lowered or "send email" in lowered or "compose email" in lowered:
            return await draft_and_send(
                self.mcp_manager,
                self.state_machine,
                self._dictated_email_from_task(text),
            )
        if "reply_to_thread" in lowered or "reply to" in lowered:
            identifier, intent = self._reply_parts_from_task(text)
            return await reply_to_thread(
                self.mcp_manager,
                self.state_machine,
                identifier,
                intent,
            )
        if "triage" in lowered or "inbox" in lowered or "urgent email" in lowered or "read email" in lowered or "emails" in lowered:
            return {
                "success": True,
                "task": "triage_inbox",
                "buckets": await triage_inbox(self.mcp_manager),
            }
        if "summarize" in lowered or "summary" in lowered or "thread" in lowered:
            identifier = self._thread_identifier_from_task(text)
            return {
                "success": True,
                "task": "summarize_thread",
                **await summarize_thread(self.mcp_manager, identifier),
            }

        return {
            "success": False,
            "message": "Email task is not implemented yet. Phase 1 currently supports inbox triage, thread summaries, and urgency preference learning only.",
        }

    async def _run_slack(self, task: str, context: str) -> dict[str, Any]:
        if self.mcp_manager is None:
            raise RuntimeError("MCP manager is not initialized.")

        text = " ".join(part for part in (task, context) if part).strip()
        lowered = text.lower()
        if any(phrase in lowered for phrase in ("read", "summary", "summarize", "messages")) and not any(
            phrase in lowered for phrase in ("send", "post")
        ):
            return {
                "success": True,
                "task": "read_slack_messages",
                "channels": await read_messages(self.mcp_manager),
            }
        if any(phrase in lowered for phrase in ("send", "post", "message")):
            channel, content = self._slack_parts_from_task(text)
            return await send_message(self.mcp_manager, self.state_machine, channel, content)

        return {
            "success": False,
            "message": "Slack task is not implemented for that request yet.",
        }

    async def _run_calendar(self, task: str, context: str) -> dict[str, Any]:
        if self.mcp_manager is None:
            raise RuntimeError("MCP manager is not initialized.")

        text = " ".join(part for part in (task, context) if part).strip()
        lowered = text.lower()
        if any(phrase in lowered for phrase in ("schedule", "calendar", "events", "what do i have")) and not any(
            phrase in lowered for phrase in ("book", "reschedule", "free", "slot")
        ):
            date = self._date_from_task(text)
            return {
                "success": True,
                "task": "get_schedule",
                "date": date,
                "events": await get_schedule(self.mcp_manager, date),
            }
        if any(phrase in lowered for phrase in ("free", "slot", "availability")):
            attendee, timeframe = self._free_slot_parts_from_task(text)
            return {
                "success": True,
                "task": "find_free_slots",
                "attendee": attendee,
                "timeframe": timeframe,
                "slots": await find_free_slots(self.mcp_manager, attendee, timeframe),
            }
        if "book" in lowered or "create meeting" in lowered or "schedule meeting" in lowered:
            slot, attendee, title = self._book_meeting_parts_from_task(text)
            return await book_meeting(self.mcp_manager, self.state_machine, slot, attendee, title)
        if "reschedule" in lowered or "move meeting" in lowered:
            meeting_identifier, new_slot = self._reschedule_parts_from_task(text)
            return await reschedule_meeting(self.mcp_manager, self.state_machine, meeting_identifier, new_slot)

        return {
            "success": False,
            "message": "Calendar task is not implemented for that request yet.",
        }

    @staticmethod
    def _dictated_email_from_task(text: str) -> dict[str, str]:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return {
                "recipient": str(parsed.get("recipient", "")),
                "subject": str(parsed.get("subject", "")),
                "body": str(parsed.get("body", "")),
            }

        def extract(pattern: str) -> str:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            return match.group(1).strip() if match else ""

        return {
            "recipient": extract(r"recipient[:=]\s*(.+?)(?:\n| subject[:=]| body[:=]|$)"),
            "subject": extract(r"subject[:=]\s*(.+?)(?:\n| body[:=]|$)"),
            "body": extract(r"body[:=]\s*(.+)$") or text,
        }

    @staticmethod
    def _is_email_access_check(lowered: str) -> bool:
        access_phrases = (
            "access to my gmail",
            "gmail access",
            "connected to gmail",
            "gmail connected",
            "can you access gmail",
            "do you have access",
        )
        return any(phrase in lowered for phrase in access_phrases) and "gmail" in lowered

    @staticmethod
    def _reply_parts_from_task(text: str) -> tuple[str, str]:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return str(parsed.get("identifier", "")), str(parsed.get("dictated_intent", ""))

        identifier = ""
        intent = text
        match = re.search(r"reply(?:_to_thread| to)?\s+(.+?)(?:\s+with\s+|\s+intent[:=]\s+)(.+)$", text, flags=re.IGNORECASE | re.DOTALL)
        if match:
            identifier = match.group(1).strip()
            intent = match.group(2).strip()
        return identifier or text, intent

    @staticmethod
    def _thread_identifier_from_task(text: str) -> str:
        patterns = (
            r"(?:summarize|summary of)\s+(?:email\s+)?thread\s+(?:from|about|with|for)?\s*(.+)",
            r"(?:summarize|summary of)\s+(.+)",
        )
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match and match.group(1).strip():
                return match.group(1).strip()
        return text.strip()

    @staticmethod
    def _slack_parts_from_task(text: str) -> tuple[str, str]:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return str(parsed.get("channel") or parsed.get("recipient") or ""), str(
                parsed.get("content") or parsed.get("message") or parsed.get("body") or ""
            )

        match = re.search(
            r"(?:send|post)(?:\s+(?:a\s+)?slack\s+message)?\s+(?:to\s+)?(.+?)(?:\s+message[:=]|\s+saying\s+|\s+that\s+|\s*:\s*)(.+)$",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            return match.group(1).strip(), match.group(2).strip()
        return "", text

    @staticmethod
    def _date_from_task(text: str) -> str:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return str(parsed.get("date") or parsed.get("day") or "today")
        lowered = text.lower()
        if "tomorrow" in lowered:
            return "tomorrow"
        return "today"

    @staticmethod
    def _free_slot_parts_from_task(text: str) -> tuple[str, str]:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return str(parsed.get("attendee") or parsed.get("person") or ""), str(
                parsed.get("timeframe") or parsed.get("date") or "next 7 days"
            )
        attendee = ""
        match = re.search(r"(?:with|for)\s+(.+?)(?:\s+(?:this|next|tomorrow|today)\b|$)", text, flags=re.IGNORECASE)
        if match:
            attendee = match.group(1).strip()
        timeframe = "next 7 days"
        for phrase in ("today", "tomorrow", "this week", "next week"):
            if phrase in text.lower():
                timeframe = phrase
                break
        return attendee, timeframe

    @staticmethod
    def _book_meeting_parts_from_task(text: str) -> tuple[dict[str, str], str, str]:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            slot = parsed.get("slot") if isinstance(parsed.get("slot"), dict) else {}
            return (
                {"slot_label": str(slot.get("slot_label", "")), "start": str(slot.get("start", "")), "end": str(slot.get("end", ""))},
                str(parsed.get("attendee") or parsed.get("person") or ""),
                str(parsed.get("title") or "Meeting"),
            )
        return {"slot_label": "", "start": "", "end": ""}, "", "Meeting"

    @staticmethod
    def _reschedule_parts_from_task(text: str) -> tuple[str, dict[str, str]]:
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            slot = parsed.get("new_slot") if isinstance(parsed.get("new_slot"), dict) else parsed.get("slot")
            slot = slot if isinstance(slot, dict) else {}
            return (
                str(parsed.get("meeting_identifier") or parsed.get("meeting") or parsed.get("title") or ""),
                {"slot_label": str(slot.get("slot_label", "")), "start": str(slot.get("start", "")), "end": str(slot.get("end", ""))},
            )
        return text, {"slot_label": "", "start": "", "end": ""}

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
