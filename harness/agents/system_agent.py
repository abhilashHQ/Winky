import logging
from typing import Any

import pywintypes
import win32api
import win32con

logger = logging.getLogger(__name__)

APP_ALIASES = {
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "notepad": "notepad.exe",
    "paint": "mspaint.exe",
    "mspaint": "mspaint.exe",
}


def launch_app(payload: dict[str, str]) -> dict[str, Any]:
    app_name = payload.get("app_name", "").strip()
    if not app_name:
        return {
            "success": False,
            "message": "No app name provided.",
        }

    executable = APP_ALIASES.get(app_name.lower(), app_name)
    try:
        result = win32api.ShellExecute(
            0,
            "open",
            executable,
            None,
            None,
            win32con.SW_SHOWNORMAL,
        )
    except pywintypes.error as exc:
        logger.exception("ShellExecute failed for app_name=%s", app_name)
        return {
            "success": False,
            "message": f"Failed to open {app_name}: {exc}.",
        }

    if result <= 32:
        return {
            "success": False,
            "message": f"Failed to open {app_name}: ShellExecute returned {result}.",
        }

    return {
        "success": True,
        "message": f"Done — {app_name} is open.",
    }
