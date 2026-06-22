import asyncio
import logging

from fastapi import FastAPI

from mcp_manager import MCPManager, ensure_windows_subprocess_policy
from secrets import fetch_secrets, get_all
from session import RT2Session
from state import StateMachine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    force=True,
)
logger = logging.getLogger(__name__)
ensure_windows_subprocess_policy()

app = FastAPI(title="Winky Harness")
state_machine = StateMachine()
rt2_session = RT2Session(state_machine=state_machine)
mcp_manager: MCPManager | None = None


@app.on_event("startup")
async def startup() -> None:
    global mcp_manager
    await fetch_secrets()
    mcp_manager = MCPManager(get_all(), result_queue=rt2_session.result_queue)
    rt2_session.interceptor.agent_runner.set_mcp_manager(mcp_manager)
    logger.info("Starting Winky harness")
    await state_machine.start_ui_server(port=8766)
    rt2_session.start_background()


@app.on_event("shutdown")
async def shutdown() -> None:
    logger.info("Stopping Winky harness")
    await rt2_session.stop()
    if mcp_manager is not None:
        await mcp_manager.shutdown()
    await state_machine.stop_ui_server()


@app.get("/health")
async def health() -> dict[str, bool | str]:
    return {
        "running": rt2_session.running,
        "ready": rt2_session.ready.is_set(),
        "state": state_machine.state.value,
        "sub_agent_running": state_machine.sub_agent_running,
    }
