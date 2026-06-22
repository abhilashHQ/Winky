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

import interceptor
from canvas_types import normalize_canvas_step
from interceptor import ToolInterceptor
from result_queue import ResultQueue
from screen_context import find_monitor_for_point
from state import StateMachine


def test_find_monitor_for_point_uses_monitor_containing_cursor():
    monitors = [
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 1920, "top": 0, "width": 1280, "height": 720},
    ]

    assert find_monitor_for_point(monitors, 2200, 100) == monitors[1]


def test_find_monitor_for_point_falls_back_to_first_monitor():
    monitors = [
        {"left": 0, "top": 0, "width": 1920, "height": 1080},
        {"left": 1920, "top": 0, "width": 1280, "height": 720},
    ]

    assert find_monitor_for_point(monitors, -50, -50) == monitors[0]


def test_normalize_canvas_step_wraps_single_annotation():
    step = normalize_canvas_step({"kind": "text", "x": 10, "y": 20, "text": "a"}, width=100, height=100)

    assert step == {
        "annotations": [
            {
                "kind": "text",
                "x": 10,
                "y": 20,
                "text": "a",
                "style": {},
            }
        ]
    }


def test_normalize_canvas_step_clamps_rect_to_bounds():
    step = normalize_canvas_step(
        {
            "annotations": [
                {"kind": "rect", "x": -10, "y": 5, "w": 150, "h": 40, "style": {"stroke": "cyan"}}
            ]
        },
        width=100,
        height=60,
    )

    assert step["annotations"] == [
        {
            "kind": "rect",
            "x": 0,
            "y": 5,
            "w": 100,
            "h": 40,
            "style": {"stroke": "cyan"},
        }
    ]


def test_normalize_canvas_step_drops_unsupported_kind():
    step = normalize_canvas_step(
        {
            "annotations": [
                {"kind": "sparkle", "x": 1, "y": 2},
                {"kind": "clear"},
            ]
        }
    )

    assert step == {"annotations": [{"kind": "clear", "style": {}}]}


def test_normalize_canvas_step_keeps_triangle_points():
    step = normalize_canvas_step(
        {
            "kind": "triangle",
            "points": [
                {"x": -5, "y": 0},
                {"x": 50, "y": 120},
                {"x": 90, "y": 10},
            ],
        },
        width=100,
        height=100,
    )

    assert step["annotations"] == [
        {
            "kind": "triangle",
            "points": [
                {"x": 0, "y": 0},
                {"x": 50, "y": 100},
                {"x": 90, "y": 10},
            ],
            "style": {},
        }
    ]


@pytest.mark.asyncio
async def test_state_machine_broadcasts_canvas_event(monkeypatch):
    state_machine = StateMachine()
    sent = []

    async def fake_broadcast(message):
        sent.append(message)

    monkeypatch.setattr(state_machine, "_broadcast", fake_broadcast)

    await state_machine.broadcast_canvas_event(
        {
            "type": "draw_annotation",
            "step": {"annotations": [{"kind": "clear", "style": {}}]},
        }
    )

    assert sent == [
        {
            "type": "draw_annotation",
            "step": {"annotations": [{"kind": "clear", "style": {}}]},
        }
    ]


class FakeToolWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, message):
        self.sent.append(json.loads(message))


@pytest.mark.asyncio
async def test_get_screen_context_returns_capture(monkeypatch):
    monkeypatch.setattr(
        interceptor,
        "capture_active_monitor",
        lambda: {
            "status": "ok",
            "target": "active_monitor",
            "coordinate_space": "monitor_relative",
            "monitor": {"x": 0, "y": 0, "width": 100, "height": 80, "scale_factor": 1.0},
            "image": {"mime_type": "image/png", "base64": "abc"},
        },
    )
    state_machine = StateMachine()
    tool_interceptor = ToolInterceptor(ResultQueue(), state_machine)
    ws = FakeToolWebSocket()

    await tool_interceptor.handle_function_call(
        ws,
        {
            "type": "response.function_call",
            "name": "get_screen_context",
            "call_id": "call-screen",
            "arguments": {},
        },
    )

    output = json.loads(ws.sent[0]["item"]["output"])
    assert output["status"] == "ok"
    assert output["monitor"]["width"] == 100


@pytest.mark.asyncio
async def test_get_screen_context_returns_error_on_capture_failure(monkeypatch):
    def fail_capture():
        raise RuntimeError("screen blocked")

    monkeypatch.setattr(interceptor, "capture_active_monitor", fail_capture)
    state_machine = StateMachine()
    tool_interceptor = ToolInterceptor(ResultQueue(), state_machine)
    ws = FakeToolWebSocket()

    await tool_interceptor.handle_function_call(
        ws,
        {
            "type": "response.function_call",
            "name": "get_screen_context",
            "call_id": "call-screen",
            "arguments": {},
        },
    )

    output = json.loads(ws.sent[0]["item"]["output"])
    assert output == {"status": "error", "message": "I couldn't capture the screen."}


@pytest.mark.asyncio
async def test_draw_annotation_broadcasts_normalized_step(monkeypatch):
    state_machine = StateMachine()
    sent = []

    async def fake_canvas_event(message):
        sent.append(message)

    monkeypatch.setattr(state_machine, "broadcast_canvas_event", fake_canvas_event)
    tool_interceptor = ToolInterceptor(ResultQueue(), state_machine)
    ws = FakeToolWebSocket()

    await tool_interceptor.handle_function_call(
        ws,
        {
            "type": "response.function_call",
            "name": "draw_annotation",
            "call_id": "call-draw",
            "arguments": {"step": {"kind": "text", "x": 5, "y": 7, "text": "c"}},
        },
    )

    assert sent == [
        {
            "type": "draw_annotation",
            "step": {
                "annotations": [
                    {"kind": "text", "x": 5, "y": 7, "text": "c", "style": {}}
                ]
            },
        }
    ]
    output = json.loads(ws.sent[0]["item"]["output"])
    assert output == {"status": "ok", "drawn": 1}
