# Winky — Windows Voice Assistant

Voice in, action out. GPT-Realtime-2 is the brain. The harness manages sub-agent lifecycle only. Speed is the leverage.

@docs/ARCHITECTURE.md
@docs/PHASE0.md

## Tech Stack

| Layer | Tech |
|---|---|
| Harness | Python 3.12, FastAPI, asyncio, websockets |
| UI Shell | Electron 33, React 19, Tailwind CSS |
| Brain / Voice / Vision | Azure GPT-Realtime-2 (WebSocket) |
| Sub-agent reasoning | Azure GPT-5.5, GPT-4o-mini |
| Win32 | pywin32, comtypes |
| STT (dictation only) | Azure Cognitive Services Speech SDK |

## Directory Layout

```
winky/
├── harness/
│   ├── main.py            # FastAPI entry + WebSocket bridge to UI
│   ├── session.py         # RT2 WebSocket session manager
│   ├── state.py           # Harness state machine (IDLE/LISTENING/THINKING/SPEAKING)
│   ├── interceptor.py     # RT2 tool call interceptor
│   ├── agent_runner.py    # Sub-agent lifecycle (spawn, async run, queue result)
│   ├── result_queue.py    # FIFO queue, injects on response.done
│   ├── dictation.py       # Azure STT streaming session
│   ├── canvas.py          # Canvas step dispatcher
│   └── agents/
│       └── system_agent.py  # Win32 system agent (Phase 0 — app launch)
├── ui/
│   ├── main.js            # Electron main process, global hotkeys, window manager
│   ├── preload.js         # Context bridge (ipcRenderer exposed to renderer)
│   └── renderer/
│       ├── App.jsx
│       ├── Notch.jsx      # Notch UI state machine (maps harness states to visuals)
│       └── Canvas.jsx     # Transparent overlay canvas
├── docs/
│   ├── ARCHITECTURE.md
│   └── PHASE0.md
└── .claude/
    ├── rules/
    │   ├── harness.md
    │   ├── ui.md
    │   └── rt2.md
    └── skills/
        └── phase0-checklist.md
```

## Commands

```bash
# Harness (run from repo root)
cd harness && pip install -r requirements.txt
cd harness && uvicorn main:app --reload --port 8765

# UI (run from repo root)
cd ui && npm install
cd ui && npm run dev

# Both together (from repo root)
npm run start   # concurrently runs harness + electron
```

## Environment Variables

All in `harness/.env` — gitignored, never committed.

```
AZURE_RT2_ENDPOINT=
AZURE_RT2_API_KEY=
AZURE_RT2_DEPLOYMENT=gpt-4o-realtime-preview
AZURE_GPT55_ENDPOINT=
AZURE_GPT55_API_KEY=
AZURE_GPT55_DEPLOYMENT=
AZURE_MINI_ENDPOINT=
AZURE_MINI_API_KEY=
AZURE_MINI_DEPLOYMENT=
AZURE_STT_KEY=
AZURE_STT_REGION=
```

## Hard Invariants — Never Violate

- Azure OpenAI only. No Anthropic, Gemini, or any other provider.
- No LangGraph, no LangChain, no agent framework. Custom harness only.
- Win32/COM actions never route through MCP. MCP is cloud/web services only.
- RT2 is the decision maker. Harness does not reason or plan.
- `spawn_agent` ACK must return in under 50ms. Never await the sub-agent before ACKing.
- Inject queued results only on `response.done`. Never interrupt RT2 mid-turn.
- Sub-agents receive only the tool set mapped to their hint. Never pass the full tool manifest.
- Result queue is FIFO. No priority overrides.
- Approval gate is mandatory before any irreversible action (send, delete, post).

## Code Conventions

- Python: black, type hints on all signatures, async/await everywhere in harness
- All WebSocket message handlers: log `msg["type"]` before processing
- React: functional components + hooks only, no class components
- Styling: Tailwind only — no inline styles, no CSS modules
- IPC between harness and Electron: JSON over localhost WebSocket on port 8766