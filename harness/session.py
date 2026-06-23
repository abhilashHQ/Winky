import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

import sounddevice as sd
from interceptor import ToolInterceptor
from secrets import get as get_secret
from result_queue import ResultQueue
from state import HarnessState, StateMachine
from websockets.legacy.client import connect

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24_000
CHANNELS = 1
AUDIO_DTYPE = "int16"
AUDIO_BLOCKSIZE = 2_400

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "spawn_agent",
        "description": "Spawn a background sub-agent for a task. Returns immediately. Result delivered when agent completes.",
        "parameters": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What to do"},
                "context": {"type": "string", "description": "Relevant context"},
                "hint": {
                    "type": "string",
                    "description": "Routing hint",
                    "enum": [
                        "system",
                        "filesystem",
                        "document",
                        "email",
                        "calendar",
                        "slack",
                        "notion",
                        "linear",
                        "github",
                        "spotify",
                        "browser",
                        "canvas_vision",
                        "composio",
                    ],
                },
            },
            "required": ["task", "context", "hint"],
        },
    },
    {
        "type": "function",
        "name": "get_screen_context",
        "description": "Capture current screen state and return as image context.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "draw_annotation",
        "description": "Draw one monitor-relative annotation step on the transparent canvas overlay. The step may contain one annotation or an annotations array.",
        "parameters": {
            "type": "object",
            "properties": {
                "step": {
                    "type": "object",
                    "description": "One annotation object or {annotations:[...]}. Supported kinds: line, arrow, rect, circle, text, formula, triangle, clear.",
                }
            },
            "required": ["step"],
        },
    },
    {
        "type": "function",
        "name": "start_dictation",
        "description": "Open Azure STT stream for long-form voice input (email body, Slack message, etc). Suspends RT2 audio tokens.",
        "parameters": {
            "type": "object",
            "properties": {
                "context": {
                    "type": "string",
                    "description": "What is being dictated and for what purpose",
                }
            },
            "required": ["context"],
        },
    },
    {
        "type": "function",
        "name": "approval_gate",
        "description": "Show preview of irreversible action in UI panel. Waits for user voice confirmation.",
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string"},
                "preview": {
                    "type": "string",
                    "description": "Human-readable summary of what will happen",
                },
            },
            "required": ["action_type", "preview"],
        },
    },
]


class RT2Session:
    def __init__(
        self,
        harness_dir: Path | None = None,
        state_machine: StateMachine | None = None,
    ) -> None:
        self.harness_dir = harness_dir or Path(__file__).resolve().parent
        self.state_machine = state_machine or StateMachine()
        self.result_queue = ResultQueue()
        self.interceptor = ToolInterceptor(self.result_queue, self.state_machine)
        self.ready = asyncio.Event()
        self.running = False
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._mic_queue: asyncio.Queue[bytes] | None = None
        self._input_stream: sd.RawInputStream | None = None
        self._output_stream: sd.RawOutputStream | None = None
        self._ws: Any | None = None
        self._sent_audio_since_activation = False
        self._commit_in_progress = False
        self._input_committed_since_activation = False
        self._server_vad_response_pending = False
        self._response_active = False
        self.state_machine.set_input_control_handler(self._handle_input_control)

    def start_background(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop_event.set()
        self.running = False
        self.ready.clear()
        self._close_audio_streams()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def run(self) -> None:
        while not self._stop_event.is_set():
            self.running = True
            self.ready.clear()
            await self.state_machine.idle()
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("RT2 session failed")
            finally:
                self.ready.clear()
                self._ws = None
                self._reset_runtime_flags()
                self._close_audio_streams()

            if not self._stop_event.is_set():
                logger.info("RT2 disconnected; reconnecting in 2 seconds")
                await asyncio.sleep(2)

        self.running = False

    async def _connect_once(self) -> None:
        endpoint = get_secret("azure_rt2_endpoint").rstrip("/")
        api_key = get_secret("azure_rt2_key")
        deployment = get_secret("azure_rt2_deployment")
        ws_endpoint = self._websocket_endpoint(endpoint)
        rt2_ws_url = f"{ws_endpoint}/openai/v1/realtime?model={deployment}"
        headers = {"api-key": api_key}

        logger.info("Connecting to Azure RT2 GA endpoint")
        async with connect(rt2_ws_url, extra_headers=headers) as ws:
            self._ws = ws
            receiver_task = asyncio.create_task(self._receive_events(ws))
            ready_task = asyncio.create_task(self.ready.wait())
            mic_task: asyncio.Task[None] | None = None

            done, pending = await asyncio.wait(
                [receiver_task, ready_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if receiver_task in done:
                receiver_task.result()
            if ready_task in done:
                self._start_audio_streams()
                mic_task = asyncio.create_task(self._send_microphone_audio(ws))

            active_tasks = [receiver_task]
            if mic_task is not None:
                active_tasks.append(mic_task)

            done, pending = await asyncio.wait(
                active_tasks,
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in done:
                task.result()
            for task in [*pending, ready_task]:
                if task.done():
                    continue
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _receive_events(self, ws: Any) -> None:
        async for raw_message in ws:
            event = json.loads(raw_message)
            event_type = event["type"]
            logger.debug("RT2 -> harness: %s", event_type)

            if event_type == "session.created":
                await self._send_session_update(ws)
            elif event_type == "session.updated":
                self.ready.set()
                logger.info("RT2 session ready")
                await self.state_machine.idle()
            elif event_type == "input_audio_buffer.speech_started":
                await self.state_machine.set_speech_active(True)
                await self.state_machine.listening()
            elif event_type == "input_audio_buffer.speech_stopped":
                await self.state_machine.set_speech_active(False)
                if self.state_machine.voice_input_mode == "always_on" and not self._input_committed_since_activation:
                    logger.info("RT2 detected speech stop in always-on mode; waiting for server VAD commit")
                    self._server_vad_response_pending = self._sent_audio_since_activation
                    if not self._server_vad_response_pending and self.state_machine.state == HarnessState.LISTENING:
                        await self.state_machine.idle()
                elif self.state_machine.voice_input_mode == "always_on":
                    logger.info("RT2 detected speech stop in always-on mode after this turn was already submitted")
                elif not self._input_committed_since_activation:
                    logger.debug("RT2 detected speech stop; waiting for input release")
            elif event_type in {"input_audio_buffer.committed", "conversation.item.created"}:
                if self._server_vad_response_pending:
                    await self._create_audio_response()
            elif event_type == "response.created":
                self._response_active = True
                await self.state_machine.thinking()
            elif event_type in {"response.audio.delta", "response.output_audio.delta"}:
                self._play_audio_delta(event)
                await self.state_machine.speaking()
            elif event_type in {"response.audio.done", "response.output_audio.done"}:
                logger.debug("RT2 audio output complete")
            elif event_type in {"response.function_call", "response.output_item.done"}:
                if self._is_function_call_event(event):
                    handled_tool = await self.interceptor.handle_function_call(ws, event)
                    if handled_tool is not None:
                        self._response_active = False
                    if handled_tool == "draw_annotation":
                        await self._create_audio_response(force=True)
            elif event_type == "response.done":
                self._response_active = False
                self._reset_turn_submission_flags()
                injected = await self.result_queue.inject_next(ws)
                if not injected:
                    await self.state_machine.idle()
                    await self.state_machine.dismiss()
            elif event_type == "error":
                self._reset_runtime_flags()
                logger.error("RT2 error: %s", event)

    async def _send_session_update(self, ws: Any) -> None:
        prompt_path = self.harness_dir / "RT2_PROMPT.md"
        rt2_system_prompt = prompt_path.read_text(encoding="utf-8")
        await ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "instructions": rt2_system_prompt,
                        "output_modalities": ["audio"],
                        "audio": {
                            "input": {
                                "transcription": {"model": "whisper-1"},
                                "format": {
                                    "type": "audio/pcm",
                                    "rate": SAMPLE_RATE,
                                },
                                "turn_detection": {
                                    "type": "server_vad",
                                    "threshold": 0.5,
                                    "prefix_padding_ms": 300,
                                    "silence_duration_ms": 600,
                                    "create_response": False,
                                    "interrupt_response": False,
                                },
                            },
                            "output": {
                                "voice": "alloy",
                                "format": {
                                    "type": "audio/pcm",
                                    "rate": SAMPLE_RATE,
                                },
                            },
                        },
                        "tools": TOOL_DEFINITIONS,
                        "tool_choice": "auto",
                    },
                }
            )
        )

    async def _send_microphone_audio(self, ws: Any) -> None:
        if self._mic_queue is None:
            raise RuntimeError("Microphone queue was not initialized")

        while not self._stop_event.is_set():
            pcm_chunk = await self._mic_queue.get()
            if self.state_machine.state == HarnessState.DICTATION:
                continue
            if not self.state_machine.voice_input_active:
                continue
            await ws.send(
                json.dumps(
                    {
                        "type": "input_audio_buffer.append",
                        "audio": base64.b64encode(pcm_chunk).decode(),
                    }
                )
            )
            self._sent_audio_since_activation = True

    async def _handle_input_control(
        self,
        active: bool,
        submit: bool,
        mode: str | None,
    ) -> None:
        if active:
            self._sent_audio_since_activation = False
            self._commit_in_progress = False
            self._input_committed_since_activation = False
            self._server_vad_response_pending = False
            if mode == "push_to_talk" and self.state_machine.state == HarnessState.IDLE:
                await self.state_machine.listening()
            return

        if not submit or self._ws is None or not self.ready.is_set():
            logger.info(
                "Input release skipped: submit=%s ws_ready=%s session_ready=%s",
                submit,
                self._ws is not None,
                self.ready.is_set(),
            )
            if self.state_machine.state == HarnessState.LISTENING:
                await self.state_machine.idle()
            return

        if not self._sent_audio_since_activation:
            logger.info("Input release skipped: no audio was sent for this activation")
            if self.state_machine.state == HarnessState.LISTENING:
                await self.state_machine.idle()
            return

        await self._submit_audio_response()

    async def _submit_audio_response(self) -> None:
        if self._ws is None or not self.ready.is_set() or self._commit_in_progress:
            logger.info(
                "Audio submit skipped: ws_ready=%s session_ready=%s commit_in_progress=%s",
                self._ws is not None,
                self.ready.is_set(),
                self._commit_in_progress,
            )
            return
        if not self._sent_audio_since_activation:
            logger.info("Audio submit skipped: no audio buffered for this turn")
            if self.state_machine.state == HarnessState.LISTENING:
                await self.state_machine.idle()
            return
        self._commit_in_progress = True
        await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        await self._create_audio_response()
        logger.info("Submitted audio buffer to RT2")
        self._sent_audio_since_activation = False

    async def _create_audio_response(self, force: bool = False) -> None:
        if self._ws is None or not self.ready.is_set() or (self._input_committed_since_activation and not force) or self._response_active:
            logger.info(
                "Response create skipped: ws_ready=%s session_ready=%s input_committed=%s response_active=%s force=%s",
                self._ws is not None,
                self.ready.is_set(),
                self._input_committed_since_activation,
                self._response_active,
                force,
            )
            return
        self._input_committed_since_activation = True
        self._server_vad_response_pending = False
        self._response_active = True
        await self._ws.send(json.dumps({"type": "response.create"}))
        logger.info("Requested RT2 response")
        await self.state_machine.thinking()
        self._sent_audio_since_activation = False

    def _reset_turn_submission_flags(self) -> None:
        self._sent_audio_since_activation = False
        self._commit_in_progress = False
        self._input_committed_since_activation = False
        self._server_vad_response_pending = False

    def _reset_runtime_flags(self) -> None:
        self._reset_turn_submission_flags()
        self._response_active = False

    def _start_audio_streams(self) -> None:
        loop = asyncio.get_running_loop()
        self._mic_queue = asyncio.Queue()

        def input_callback(
            indata: bytes,
            frames: int,
            time: Any,
            status: sd.CallbackFlags,
        ) -> None:
            if status:
                logger.warning("Microphone status: %s", status)
            loop.call_soon_threadsafe(self._queue_microphone_chunk, bytes(indata))

        self._input_stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=AUDIO_DTYPE,
            blocksize=AUDIO_BLOCKSIZE,
            callback=input_callback,
        )
        self._output_stream = sd.RawOutputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype=AUDIO_DTYPE,
            blocksize=AUDIO_BLOCKSIZE,
        )
        self._input_stream.start()
        self._output_stream.start()
        logger.info("Audio streams started at %s Hz PCM16 mono", SAMPLE_RATE)

    def _queue_microphone_chunk(self, pcm_chunk: bytes) -> None:
        if self._mic_queue is not None and not self._stop_event.is_set():
            self._mic_queue.put_nowait(pcm_chunk)

    def _play_audio_delta(self, event: dict[str, Any]) -> None:
        if self._output_stream is None:
            return
        audio_delta = event.get("delta")
        if not audio_delta:
            return
        self._output_stream.write(base64.b64decode(audio_delta))

    @staticmethod
    def _is_function_call_event(event: dict[str, Any]) -> bool:
        if event["type"] == "response.function_call":
            return True
        item = event.get("item")
        return isinstance(item, dict) and item.get("type") == "function_call"

    def _close_audio_streams(self) -> None:
        for stream in (self._input_stream, self._output_stream):
            if stream is None:
                continue
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.exception("Failed to close audio stream")
        self._input_stream = None
        self._output_stream = None
        self._mic_queue = None

    @staticmethod
    def _websocket_endpoint(endpoint: str) -> str:
        if endpoint.startswith("https://"):
            return f"wss://{endpoint.removeprefix('https://')}"
        if endpoint.startswith("http://"):
            return f"ws://{endpoint.removeprefix('http://')}"
        return endpoint
