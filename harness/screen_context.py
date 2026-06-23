import base64
import logging
import time
from io import BytesIO
from typing import Any

import mss
from PIL import Image
import win32gui

logger = logging.getLogger(__name__)

MAX_REALTIME_IMAGE_LONG_EDGE = 768
REALTIME_IMAGE_JPEG_QUALITY = 55


def capture_active_monitor() -> dict[str, Any]:
    """Capture the monitor containing the cursor and return bounded image context."""
    cursor_x, cursor_y = win32gui.GetCursorPos()
    with mss.mss() as screenshotter:
        monitors = [dict(monitor) for monitor in screenshotter.monitors[1:]]
        monitor = find_monitor_for_point(monitors, cursor_x, cursor_y)
        shot = screenshotter.grab(monitor)
        image = compress_image_for_realtime(
            shot.rgb,
            shot.size,
            max_long_edge=MAX_REALTIME_IMAGE_LONG_EDGE,
            jpeg_quality=REALTIME_IMAGE_JPEG_QUALITY,
        )

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
        "image": image,
    }


def compress_image_for_realtime(
    rgb_bytes: bytes,
    size: tuple[int, int],
    max_long_edge: int = MAX_REALTIME_IMAGE_LONG_EDGE,
    jpeg_quality: int = REALTIME_IMAGE_JPEG_QUALITY,
) -> dict[str, Any]:
    """Resize and JPEG-compress RGB bytes for Azure Realtime image input."""
    image = Image.frombytes("RGB", size, rgb_bytes)
    width, height = image.size
    long_edge = max(width, height)
    if long_edge > max_long_edge:
        scale = max_long_edge / long_edge
        resized_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        image = image.resize(resized_size, Image.Resampling.LANCZOS)

    output = BytesIO()
    image.save(output, format="JPEG", quality=jpeg_quality, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return {
        "mime_type": "image/jpeg",
        "base64": encoded,
        "width": image.width,
        "height": image.height,
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
