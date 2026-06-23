import json
import sys
import types
from pathlib import Path

import pytest

HARNESS_DIR = Path(__file__).resolve().parents[1]
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

try:
    import sounddevice  # noqa: F401
except ImportError:
    sys.modules["sounddevice"] = types.SimpleNamespace(
        RawInputStream=object,
        RawOutputStream=object,
        CallbackFlags=object,
    )

try:
    import composio  # noqa: F401
except ImportError:
    composio_module = types.ModuleType("composio")
    composio_module.Composio = object
    sys.modules["composio"] = composio_module

import mcp_manager
from agent_runner import AgentRunner
from agents.email_agent import _clean_identifier
from mcp_manager import MCPManager
from session import RT2Session
from state import HarnessState


class FakeWebSocket:
    def __init__(self, events):
        self._events = list(events)
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._events:
            raise StopAsyncIteration
        return json.dumps(self._events.pop(0))

    async def send(self, message):
        self.sent.append(json.loads(message))


@pytest.mark.asyncio
async def test_always_on_server_vad_requests_response_without_manual_commit():
    session = RT2Session(harness_dir=HARNESS_DIR)
    ws = FakeWebSocket(
        [
            {"type": "input_audio_buffer.speech_stopped"},
            {"type": "input_audio_buffer.committed"},
        ]
    )
    session._ws = ws
    session.ready.set()
    session._sent_audio_since_activation = True
    session.state_machine.voice_input_mode = "always_on"

    await session._receive_events(ws)

    assert [message["type"] for message in ws.sent] == ["response.create"]


@pytest.mark.asyncio
async def test_tool_result_waits_for_response_done_before_followup(monkeypatch):
    session = RT2Session(harness_dir=HARNESS_DIR)
    ws = FakeWebSocket(
        [
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "name": "draw_annotation",
                    "call_id": "call-draw",
                    "arguments": json.dumps({"step": {"kind": "text", "x": 1, "y": 2, "text": "a"}}),
                },
            },
            {"type": "response.done"},
        ]
    )
    session._ws = ws
    session.ready.set()
    session._response_active = True

    async def fake_broadcast(message):
        return None

    monkeypatch.setattr(session.state_machine, "broadcast_canvas_event", fake_broadcast)

    await session._receive_events(ws)

    assert [message["type"] for message in ws.sent] == ["conversation.item.create", "response.create"]


@pytest.mark.asyncio
async def test_always_on_response_done_returns_to_listening_not_idle():
    session = RT2Session(harness_dir=HARNESS_DIR)
    ws = FakeWebSocket([{"type": "response.done"}])
    session.state_machine.voice_input_mode = "always_on"
    session.state_machine.voice_input_active = True

    await session._receive_events(ws)

    assert session.state_machine.state == HarnessState.LISTENING


@pytest.mark.asyncio
async def test_queued_agent_result_injects_before_tool_followup(monkeypatch):
    session = RT2Session(harness_dir=HARNESS_DIR)
    ws = FakeWebSocket([{"type": "response.done"}, {"type": "response.done"}])
    session._ws = ws
    session.ready.set()
    session._tool_result_pending_response = True
    await session.result_queue.put("call-agent", {"result": "done"})

    await session._receive_events(ws)

    assert [message["type"] for message in ws.sent] == [
        "conversation.item.create",
        "response.create",
        "response.create",
    ]


@pytest.mark.asyncio
async def test_always_on_speech_stop_with_no_audio_sends_nothing():
    session = RT2Session(harness_dir=HARNESS_DIR)
    ws = FakeWebSocket([{"type": "input_audio_buffer.speech_stopped"}])
    session._ws = ws
    session.ready.set()
    session.state_machine.voice_input_mode = "always_on"

    await session._receive_events(ws)

    assert ws.sent == []


@pytest.mark.asyncio
async def test_session_failure_resets_response_active():
    session = RT2Session(harness_dir=HARNESS_DIR)
    session._response_active = True

    session._reset_runtime_flags()

    assert session._response_active is False


@pytest.mark.asyncio
async def test_error_event_resets_response_active():
    session = RT2Session(harness_dir=HARNESS_DIR)
    ws = FakeWebSocket([{"type": "error", "error": {"message": "boom"}}])
    session._response_active = True

    await session._receive_events(ws)

    assert session._response_active is False


@pytest.mark.asyncio
async def test_always_on_server_vad_does_not_create_response_while_response_active():
    session = RT2Session(harness_dir=HARNESS_DIR)
    ws = FakeWebSocket(
        [
            {"type": "input_audio_buffer.speech_stopped"},
            {"type": "input_audio_buffer.committed"},
        ]
    )
    session._ws = ws
    session.ready.set()
    session._response_active = True
    session._sent_audio_since_activation = True
    session.state_machine.voice_input_mode = "always_on"

    await session._receive_events(ws)

    assert ws.sent == []


def test_composio_hints_use_hint_scoped_process_keys():
    assert MCPManager._process_key_for_hint("email") == "email"
    assert MCPManager._process_key_for_hint("slack") == "slack"
    assert MCPManager._process_key_for_hint("calendar") == "calendar"
    assert MCPManager._process_key_for_hint("spotify") == "spotify"


def test_email_access_check_routes_before_summary_or_inbox():
    assert AgentRunner._is_email_access_check("do you have access to my gmail")
    assert AgentRunner._is_email_access_check("is gmail connected")


def test_clean_identifier_drops_generic_gmail_requests():
    assert _clean_identifier("do you have access to my gmail") == ""
    assert _clean_identifier("summarize email thread from Alice") == "from Alice"


def test_email_tool_router_session_does_not_request_spotify(monkeypatch):
    created_toolkits = []

    class FakeComposio:
        def __init__(self, api_key):
            self.api_key = api_key

        def create(self, user_id, toolkits):
            created_toolkits.append(toolkits)
            return types.SimpleNamespace(
                mcp=types.SimpleNamespace(
                    url="https://example.test/mcp",
                    headers={"Authorization": "Bearer test"},
                )
            )

    monkeypatch.setattr(mcp_manager, "Composio", FakeComposio)
    manager = MCPManager({"composio_api_key": "test-key"})

    manager._get_composio_mcp_config(manager._process_key_for_hint("email"))

    assert created_toolkits == [["gmail"]]
