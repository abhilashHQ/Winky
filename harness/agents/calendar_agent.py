import json
import re
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from agents.composio_tools import MUTATING_KEYWORDS, args_for_tool, call_selected_tool, coerce_json, extract_payload, select_tool
from mcp_manager import MCPManager
from secrets import get as get_secret
from state import StateMachine

LOCAL_TZ = datetime.now().astimezone().tzinfo or ZoneInfo("UTC")
BUSINESS_START_HOUR = 9
BUSINESS_END_HOUR = 17


class CalendarAgent:
    def __init__(self, mcp_manager: MCPManager, state_machine: StateMachine | None = None) -> None:
        self.mcp_manager = mcp_manager
        self.state_machine = state_machine

    async def get_schedule(self, date: str) -> list[dict[str, Any]]:
        start, end = _date_range(date)
        proc = await self.mcp_manager.get_or_spawn("calendar")
        tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("calendar",),
            preferred=("event", "list", "get"),
            avoid=MUTATING_KEYWORDS,
            error_label="Google Calendar event list",
            use_case="List Google Calendar events for a specific date range",
        )
        response = await call_selected_tool(
            self.mcp_manager,
            proc,
            tool,
            args_for_tool(
                tool,
                f"Fetch Google Calendar events from {start.isoformat()} to {end.isoformat()}. Include start, end, title, attendees. Read only.",
                date=date,
                start=start.isoformat(),
                end=end.isoformat(),
                max_results=50,
                limit=50,
            ),
            "CALENDAR_LIST_EVENTS",
        )
        events = _normalize_events(extract_payload(response))
        return await _format_schedule_with_mini(date, events)

    async def find_free_slots(self, attendee: str, timeframe: str) -> list[dict[str, str]]:
        start, end = _timeframe_range(timeframe)
        busy = await self._fetch_busy(start, end, attendee)
        return _candidate_slots(start, end, busy)[:3]

    async def book_meeting(self, slot: dict[str, str], attendee: str, title: str) -> dict[str, Any]:
        preview = f"Book '{title}' with {attendee} on {slot.get('slot_label', slot.get('start', 'the selected time'))}"
        approved = await self._request_approval(preview, "book_meeting")
        if not approved:
            return {
                "success": False,
                "cancelled": True,
                "slot": slot,
                "attendee": attendee,
                "title": title,
                "message": "Meeting booking cancelled.",
            }

        proc = await self.mcp_manager.get_or_spawn("calendar")
        tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("calendar",),
            preferred=("create", "event"),
            avoid=("delete", "remove"),
            error_label="Google Calendar create event",
            use_case="Create a Google Calendar event with attendees and send calendar invite updates",
        )
        response = await call_selected_tool(
            self.mcp_manager,
            proc,
            tool,
            args_for_tool(
                tool,
                f"Create a Google Calendar event titled {title} with attendee {attendee} from {slot['start']} to {slot['end']} and send an invite.",
                title=title,
                subject=title,
                attendee=attendee,
                start=slot["start"],
                end=slot["end"],
                send_updates=True,
                sendUpdates="all",
            ),
            "CALENDAR_CREATE_EVENT",
        )
        return {
            "success": True,
            "task": "book_meeting",
            "slot": slot,
            "attendee": attendee,
            "title": title,
            "create_result": extract_payload(response),
        }

    async def reschedule_meeting(self, meeting_identifier: str, new_slot: dict[str, str]) -> dict[str, Any]:
        event = await self._find_event(meeting_identifier)
        event_title = event.get("title") or meeting_identifier
        preview = f"Reschedule '{event_title}' to {new_slot.get('slot_label', new_slot.get('start', 'the selected time'))}"
        approved = await self._request_approval(preview, "reschedule_meeting")
        if not approved:
            return {
                "success": False,
                "cancelled": True,
                "meeting": event,
                "slot": new_slot,
                "message": "Meeting reschedule cancelled.",
            }

        proc = await self.mcp_manager.get_or_spawn("calendar")
        tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("calendar",),
            preferred=("update", "event"),
            avoid=("delete", "remove"),
            error_label="Google Calendar update event",
            use_case="Update or reschedule an existing Google Calendar event to a new start and end time",
        )
        response = await call_selected_tool(
            self.mcp_manager,
            proc,
            tool,
            args_for_tool(
                tool,
                f"Update Google Calendar event {event['id']} to start {new_slot['start']} and end {new_slot['end']}. Send update notifications.",
                event_id=event["id"],
                title=event_title,
                start=new_slot["start"],
                end=new_slot["end"],
                send_updates=True,
                sendUpdates="all",
            ),
            "CALENDAR_UPDATE_EVENT",
        )
        return {
            "success": True,
            "task": "reschedule_meeting",
            "meeting": event,
            "slot": new_slot,
            "update_result": extract_payload(response),
        }

    async def _fetch_busy(self, start: datetime, end: datetime, attendee: str) -> list[tuple[datetime, datetime]]:
        proc = await self.mcp_manager.get_or_spawn("calendar")
        tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("calendar",),
            preferred=("free", "busy", "availability"),
            avoid=MUTATING_KEYWORDS,
            error_label="Google Calendar freebusy",
            use_case="Fetch Google Calendar free busy or availability for user and attendee over a time range",
        )
        response = await call_selected_tool(
            self.mcp_manager,
            proc,
            tool,
            args_for_tool(
                tool,
                f"Fetch busy times for the primary calendar and attendee {attendee} from {start.isoformat()} to {end.isoformat()}. Read only.",
                attendee=attendee,
                start=start.isoformat(),
                end=end.isoformat(),
            ),
            "CALENDAR_FETCH_BUSY",
        )
        return _normalize_busy(extract_payload(response))

    async def _find_event(self, meeting_identifier: str) -> dict[str, str]:
        start, end = _timeframe_range("next 30 days")
        proc = await self.mcp_manager.get_or_spawn("calendar")
        tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("calendar",),
            preferred=("event", "search", "list"),
            avoid=MUTATING_KEYWORDS,
            error_label="Google Calendar event search",
            use_case="Search Google Calendar events by title or description in a date range",
        )
        response = await call_selected_tool(
            self.mcp_manager,
            proc,
            tool,
            args_for_tool(
                tool,
                f"Find the Google Calendar event matching this name or description: {meeting_identifier}. Search from {start.isoformat()} to {end.isoformat()}. Read only.",
                query=meeting_identifier,
                start=start.isoformat(),
                end=end.isoformat(),
                max_results=10,
                limit=10,
            ),
            "CALENDAR_SEARCH_EVENTS",
        )
        events = _normalize_events(extract_payload(response))
        if not events:
            raise ValueError(f"No calendar event found for: {meeting_identifier}")
        return events[0]

    async def _request_approval(self, preview: str, action_type: str) -> bool:
        if self.state_machine is None:
            raise RuntimeError("Approval requires StateMachine.")
        response = await self.state_machine.request_approval(
            call_id=f"calendar-{uuid.uuid4()}",
            preview=preview,
            action_type=action_type,
            timeout=60.0,
        )
        return response is not None and bool(response.get("approved"))


async def get_schedule(mcp_manager: MCPManager, date: str) -> list[dict[str, Any]]:
    return await CalendarAgent(mcp_manager).get_schedule(date)


async def find_free_slots(mcp_manager: MCPManager, attendee: str, timeframe: str) -> list[dict[str, str]]:
    return await CalendarAgent(mcp_manager).find_free_slots(attendee, timeframe)


async def book_meeting(
    mcp_manager: MCPManager,
    state_machine: StateMachine,
    slot: dict[str, str],
    attendee: str,
    title: str,
) -> dict[str, Any]:
    return await CalendarAgent(mcp_manager, state_machine).book_meeting(slot, attendee, title)


async def reschedule_meeting(
    mcp_manager: MCPManager,
    state_machine: StateMachine,
    meeting_identifier: str,
    new_slot: dict[str, str],
) -> dict[str, Any]:
    return await CalendarAgent(mcp_manager, state_machine).reschedule_meeting(meeting_identifier, new_slot)


async def _format_schedule_with_mini(date: str, events: list[dict[str, str]]) -> list[dict[str, Any]]:
    if not events:
        return []

    endpoint = get_secret("azure_mini_endpoint").rstrip("/")
    api_key = get_secret("azure_mini_key")
    deployment = get_secret("azure_mini_deployment")
    url = f"{endpoint}/openai/v1/chat/completions"
    payload = {
        "model": deployment,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Format calendar events for a voice assistant. "
                    "Return JSON only: {\"events\":[{\"time\":string,\"title\":string,\"attendees\":[string]}]}"
                ),
            },
            {"role": "user", "content": json.dumps({"date": date, "events": events}, ensure_ascii=False)},
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()

    parsed = json.loads(response.json()["choices"][0]["message"]["content"])
    formatted = parsed.get("events", [])
    if not isinstance(formatted, list):
        return events
    return [_normalize_formatted_event(event) for event in formatted if isinstance(event, dict)]


def _date_range(date_text: str) -> tuple[datetime, datetime]:
    start = _parse_date(date_text)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _timeframe_range(timeframe: str) -> tuple[datetime, datetime]:
    now = datetime.now(LOCAL_TZ)
    lowered = timeframe.lower().strip()
    days = 7
    if "today" in lowered:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    if "tomorrow" in lowered:
        start = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)
    match = re.search(r"next\s+(\d+)\s+days", lowered)
    if match:
        days = max(1, int(match.group(1)))
    return now, now + timedelta(days=days)


def _parse_date(date_text: str) -> datetime:
    now = datetime.now(LOCAL_TZ)
    lowered = date_text.lower().strip()
    if not lowered or lowered == "today":
        return now
    if lowered == "tomorrow":
        return now + timedelta(days=1)
    try:
        parsed = datetime.fromisoformat(date_text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_TZ)
        return parsed.astimezone(LOCAL_TZ)
    except ValueError:
        return now


def _candidate_slots(
    start: datetime,
    end: datetime,
    busy: list[tuple[datetime, datetime]],
) -> list[dict[str, str]]:
    slots: list[dict[str, str]] = []
    cursor = max(start, datetime.now(LOCAL_TZ)).replace(minute=0, second=0, microsecond=0)
    if cursor.hour < BUSINESS_START_HOUR:
        cursor = cursor.replace(hour=BUSINESS_START_HOUR)
    if cursor.hour >= BUSINESS_END_HOUR:
        cursor = (cursor + timedelta(days=1)).replace(hour=BUSINESS_START_HOUR)

    while cursor + timedelta(minutes=30) <= end and len(slots) < 3:
        if cursor.weekday() >= 5 or cursor.hour >= BUSINESS_END_HOUR:
            cursor = (cursor + timedelta(days=1)).replace(hour=BUSINESS_START_HOUR)
            continue
        slot_end = cursor + timedelta(minutes=30)
        if slot_end.hour <= BUSINESS_END_HOUR and not _overlaps_busy(cursor, slot_end, busy):
            slots.append(
                {
                    "slot_label": _slot_label(cursor),
                    "start": cursor.isoformat(),
                    "end": slot_end.isoformat(),
                }
            )
        cursor += timedelta(minutes=30)
    return slots


def _overlaps_busy(start: datetime, end: datetime, busy: list[tuple[datetime, datetime]]) -> bool:
    return any(start < busy_end and end > busy_start for busy_start, busy_end in busy)


def _slot_label(value: datetime) -> str:
    label = value.strftime("%A %I:%M %p")
    return label.replace(" 0", " ")


def _normalize_events(payload: Any) -> list[dict[str, str]]:
    payload = coerce_json(payload)
    if isinstance(payload, dict):
        items = payload.get("events") or payload.get("items") or payload.get("data") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    events: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        start = item.get("start")
        end = item.get("end")
        events.append(
            {
                "id": str(item.get("id") or item.get("event_id") or item.get("eventId") or ""),
                "title": str(item.get("title") or item.get("summary") or item.get("name") or "(no title)"),
                "start": _time_value(start),
                "end": _time_value(end),
                "attendees": _attendee_names(item.get("attendees") or item.get("participants") or []),
                "description": str(item.get("description") or ""),
            }
        )
    return events


def _normalize_busy(payload: Any) -> list[tuple[datetime, datetime]]:
    payload = coerce_json(payload)
    busy_items: list[Any] = []
    if isinstance(payload, dict):
        if isinstance(payload.get("busy"), list):
            busy_items = payload["busy"]
        elif isinstance(payload.get("calendars"), dict):
            for calendar in payload["calendars"].values():
                if isinstance(calendar, dict) and isinstance(calendar.get("busy"), list):
                    busy_items.extend(calendar["busy"])
        elif isinstance(payload.get("data"), list):
            busy_items = payload["data"]
    elif isinstance(payload, list):
        busy_items = payload

    busy: list[tuple[datetime, datetime]] = []
    for item in busy_items:
        if not isinstance(item, dict):
            continue
        start = _parse_iso_time(str(item.get("start") or item.get("start_time") or ""))
        end = _parse_iso_time(str(item.get("end") or item.get("end_time") or ""))
        if start is not None and end is not None:
            busy.append((start, end))
    return busy


def _parse_iso_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TZ)
    return parsed.astimezone(LOCAL_TZ)


def _time_value(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("dateTime") or value.get("date") or value.get("time") or "")
    return str(value or "")


def _attendee_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    attendees: list[str] = []
    for item in value:
        if isinstance(item, dict):
            attendees.append(str(item.get("email") or item.get("name") or item.get("displayName") or ""))
        else:
            attendees.append(str(item))
    return [attendee for attendee in attendees if attendee]


def _normalize_formatted_event(event: dict[str, Any]) -> dict[str, Any]:
    attendees = event.get("attendees", [])
    return {
        "time": str(event.get("time", "")),
        "title": str(event.get("title", "(no title)")),
        "attendees": attendees if isinstance(attendees, list) else [],
    }
