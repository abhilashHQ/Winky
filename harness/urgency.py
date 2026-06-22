import json
from pathlib import Path
from typing import Any

import httpx

from secrets import get as get_secret

MEMORY_PATH = Path(__file__).resolve().parents[1] / "MEMORY.md"
VALID_URGENCY = {"high", "medium", "low"}


async def classify(emails: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify emails by urgency using memory-informed GPT-4o-mini scoring."""
    if not emails:
        return []

    normalized_emails = [_normalize_email(email) for email in emails]
    memory = _load_memory()
    try:
        classifications = await _classify_with_model(normalized_emails, memory)
    except Exception as exc:
        return [
            {
                "email": email,
                "urgency": "medium",
                "reason": f"Classifier unavailable: {exc}",
            }
            for email in normalized_emails
        ]

    by_index = {
        item.get("index"): item
        for item in classifications
        if isinstance(item, dict) and isinstance(item.get("index"), int)
    }
    results: list[dict[str, Any]] = []
    for index, email in enumerate(normalized_emails):
        item = by_index.get(index, {})
        urgency = str(item.get("urgency", "medium")).lower()
        if urgency not in VALID_URGENCY:
            urgency = "medium"
        reason = str(item.get("reason", "No reason provided."))
        results.append(
            {
                "email": email,
                "urgency": urgency,
                "reason": reason,
            }
        )
    return results


def learn_preference(correction: str) -> dict[str, str]:
    """Append a learned urgency preference to MEMORY.md."""
    cleaned = correction.strip()
    if not cleaned:
        raise ValueError("Correction cannot be empty.")

    MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with MEMORY_PATH.open("a", encoding="utf-8") as file:
        file.write(f"\n- Urgency preference: {cleaned}\n")

    return {
        "success": True,
        "message": "Learned urgency preference.",
        "correction": cleaned,
    }


async def _classify_with_model(
    emails: list[dict[str, str]],
    memory: str,
) -> list[dict[str, Any]]:
    endpoint = get_secret("azure_mini_endpoint").rstrip("/")
    api_key = get_secret("azure_mini_key")
    deployment = get_secret("azure_mini_deployment")
    url = f"{endpoint}/openai/v1/chat/completions"

    payload = {
        "model": deployment,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You classify email urgency for Winky, a Windows voice assistant. "
                    "Use the user's learned preferences from MEMORY.md when relevant. "
                    "Return only JSON with this shape: "
                    '{"classifications":[{"index":0,"urgency":"high|medium|low","reason":"one short reason"}]}\n\n'
                    f"MEMORY.md:\n{memory or '(empty)'}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "emails": [
                            {
                                "index": index,
                                "subject": email["subject"],
                                "sender": email["sender"],
                                "body_preview": email["body_preview"],
                            }
                            for index, email in enumerate(emails)
                        ]
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()

    content = response.json()["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    classifications = parsed.get("classifications", [])
    if not isinstance(classifications, list):
        raise ValueError("Classifier response did not include classifications list.")
    return classifications


def _normalize_email(email: dict[str, Any]) -> dict[str, str]:
    body_preview = str(email.get("body_preview") or email.get("body") or "")[:200]
    return {
        "id": str(email.get("id", "")),
        "thread_id": str(email.get("thread_id", "")),
        "subject": str(email.get("subject", "(no subject)")),
        "sender": str(email.get("sender", "unknown sender")),
        "body_preview": body_preview,
    }


def _load_memory() -> str:
    if not MEMORY_PATH.exists():
        return ""
    return MEMORY_PATH.read_text(encoding="utf-8")
