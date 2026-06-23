import json
from typing import Any

import httpx

from canvas_types import normalize_canvas_step
from secrets import get as get_secret

SYSTEM_PROMPT = """You are Winky's canvas vision planner.
Return only JSON with this shape:
{"steps":[{"say":"short spoken line","draw":[{"kind":"text","x":10,"y":20,"text":"label"}]}]}.
Use only these annotation kinds: line, arrow, rect, circle, text, formula, triangle, clear.
All coordinates must be monitor-relative to the screenshot, with top-left as 0,0.
Keep plans short: 3 to 6 steps. Each say line must be one brief sentence.
"""


async def plan_visual_explanation(task: str, context: str) -> dict[str, Any]:
    endpoint = get_secret("azure_gpt55_endpoint").rstrip("/")
    api_key = get_secret("azure_gpt55_key")
    deployment = get_secret("azure_gpt55_deployment")
    url = f"{endpoint}/openai/v1/chat/completions"

    payload = {
        "model": deployment,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "task": task,
                        "context": context,
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            url,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    raw_steps = parsed.get("steps")
    if not isinstance(raw_steps, list):
        raise ValueError("Canvas vision response did not include steps list.")

    steps: list[dict[str, Any]] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict):
            continue
        say = str(raw_step.get("say", "")).strip()
        draw = raw_step.get("draw")
        if not isinstance(draw, list):
            draw = []
        normalized = normalize_canvas_step({"annotations": draw})
        steps.append(
            {
                "say": say,
                "draw": normalized["annotations"],
            }
        )

    return {"success": True, "steps": steps}
