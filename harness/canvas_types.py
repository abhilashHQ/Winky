import logging
from typing import Any

logger = logging.getLogger(__name__)

SUPPORTED_KINDS = {"line", "arrow", "rect", "circle", "text", "formula", "triangle", "clear"}
_POINT_KINDS = {"line", "arrow", "triangle"}
_BOX_KINDS = {"rect", "circle"}
_TEXT_KINDS = {"text", "formula"}


def normalize_canvas_step(
    raw_step: dict[str, Any],
    width: int | None = None,
    height: int | None = None,
) -> dict[str, Any]:
    """Normalize a canvas step into {"annotations": [...]} with safe primitives."""
    raw_annotations = raw_step.get("annotations")
    if not isinstance(raw_annotations, list):
        raw_annotations = [raw_step]

    annotations: list[dict[str, Any]] = []
    for raw_annotation in raw_annotations:
        if not isinstance(raw_annotation, dict):
            logger.warning("Ignoring non-object canvas annotation: %s", raw_annotation)
            continue
        normalized = _normalize_annotation(raw_annotation, width, height)
        if normalized is not None:
            annotations.append(normalized)

    return {"annotations": annotations}


def _normalize_annotation(
    raw_annotation: dict[str, Any],
    width: int | None,
    height: int | None,
) -> dict[str, Any] | None:
    kind = str(raw_annotation.get("kind", "")).strip().lower()
    if kind not in SUPPORTED_KINDS:
        logger.warning("Ignoring unsupported canvas annotation kind: %s", kind)
        return None

    style = raw_annotation.get("style")
    annotation: dict[str, Any] = {
        "kind": kind,
        "style": style if isinstance(style, dict) else {},
    }

    annotation_id = raw_annotation.get("id")
    if isinstance(annotation_id, str) and annotation_id:
        annotation["id"] = annotation_id

    if kind == "clear":
        return annotation

    if kind in _POINT_KINDS:
        points = _normalize_points(raw_annotation.get("points"), width, height)
        if not points and all(key in raw_annotation for key in ("x1", "y1", "x2", "y2")):
            points = [
                {
                    "x": _clamp_number(raw_annotation.get("x1"), 0, width),
                    "y": _clamp_number(raw_annotation.get("y1"), 0, height),
                },
                {
                    "x": _clamp_number(raw_annotation.get("x2"), 0, width),
                    "y": _clamp_number(raw_annotation.get("y2"), 0, height),
                },
            ]
        if kind == "triangle" and len(points) != 3:
            logger.warning("Ignoring triangle with %s points", len(points))
            return None
        if kind in {"line", "arrow"} and len(points) < 2:
            logger.warning("Ignoring %s with %s points", kind, len(points))
            return None
        annotation["points"] = points
        text = raw_annotation.get("text")
        if isinstance(text, str) and text:
            annotation["text"] = text
        return annotation

    if kind in _BOX_KINDS:
        box = _normalize_box(raw_annotation, width, height)
        annotation.update(box)
        return annotation

    if kind in _TEXT_KINDS:
        annotation["x"] = _clamp_number(raw_annotation.get("x"), 0, width)
        annotation["y"] = _clamp_number(raw_annotation.get("y"), 0, height)
        annotation["text"] = str(raw_annotation.get("text", ""))
        return annotation

    return annotation


def _normalize_box(
    raw_annotation: dict[str, Any],
    width: int | None,
    height: int | None,
) -> dict[str, float | int]:
    x = _clamp_number(raw_annotation.get("x"), 0, width)
    y = _clamp_number(raw_annotation.get("y"), 0, height)
    raw_w = raw_annotation.get("w", raw_annotation.get("width", 0))
    raw_h = raw_annotation.get("h", raw_annotation.get("height", 0))
    w = _clamp_number(raw_w, 0, width)
    h = _clamp_number(raw_h, 0, height)
    return {"x": x, "y": y, "w": w, "h": h}


def _normalize_points(
    raw_points: Any,
    width: int | None,
    height: int | None,
) -> list[dict[str, float | int]]:
    if not isinstance(raw_points, list):
        return []

    points: list[dict[str, float | int]] = []
    for raw_point in raw_points:
        if not isinstance(raw_point, dict):
            continue
        points.append(
            {
                "x": _clamp_number(raw_point.get("x"), 0, width),
                "y": _clamp_number(raw_point.get("y"), 0, height),
            }
        )
    return points


def _clamp_number(value: Any, minimum: float, maximum: int | None) -> float | int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum

    if maximum is not None:
        number = min(number, float(maximum))
    number = max(number, float(minimum))
    number = float(number)

    if number.is_integer():
        return int(number)
    return number
