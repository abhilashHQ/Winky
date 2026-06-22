import base64
import logging
import time
from typing import Any

import mss
import mss.tools
import win32gui

logger = logging.getLogger(__name__)


def capture_active_monitor() -> dict[str, Any]:
    """Capture the monitor containing the cursor and return base64 PNG context."""
    cursor_x, cursor_y = win32gui.GetCursorPos()
    with mss.mss() as screenshotter:
        monitors = [dict(monitor) for monitor in screenshotter.monitors[1:]]
        monitor = find_monitor_for_point(monitors, cursor_x, cursor_y)
        shot = screenshotter.grab(monitor)
        png_bytes = mss.tools.to_png(shot.rgb, shot.size)

    return {
        "status": "ok",
        "target": "active_monitor",
        "coordinate_space": "monitor_relative",
        "captured_at": time.time(),
        "monitor": {
            "x": int(monitor["left"]),
            "y": int(monitor["top"]),
            "width": int(monitor["width"]),
            "height": int(monitor["height"]),
            "scale_factor": 1.0,
        },
        "image": {
            "mime_type": "image/png",
            "base64": base64.b64encode(png_bytes).decode("ascii"),
        },
    }


def find_monitor_for_point(
    monitors: list[dict[str, int]],
    x: int,
    y: int,
) -> dict[str, int]:
    """Return monitor containing the point, falling back to the first monitor."""
    if not monitors:
        raise RuntimeError("No monitors available for screen capture.")

    for monitor in monitors:
        left = int(monitor["left"])
        top = int(monitor["top"])
        right = left + int(monitor["width"])
        bottom = top + int(monitor["height"])
        if left <= x < right and top <= y < bottom:
            return monitor

    logger.info("Cursor was outside known monitors; using first monitor")
    return monitors[0]
