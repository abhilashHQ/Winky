import json
import re
import uuid
from typing import Any

import httpx

from agents.composio_tools import MUTATING_KEYWORDS, args_for_tool, call_selected_tool, coerce_json, extract_payload, select_tool
from mcp_manager import MCPManager
from secrets import get as get_secret
from state import StateMachine


class SlackAgent:
    def __init__(self, mcp_manager: MCPManager, state_machine: StateMachine | None = None) -> None:
        self.mcp_manager = mcp_manager
        self.state_machine = state_machine

    async def read_messages(self) -> list[dict[str, Any]]:
        proc = await self.mcp_manager.get_or_spawn("slack")
        channel_tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("slack",),
            preferred=("channel", "list", "conversation"),
            avoid=MUTATING_KEYWORDS,
            error_label="Slack channel list",
            use_case="List Slack channels and DM conversations where the current user is a member",
        )
        channel_response = await call_selected_tool(
            self.mcp_manager,
            proc,
            channel_tool,
            args_for_tool(
                channel_tool,
                "List Slack channels and direct-message conversations where the user is a member. Include channel id, name, and membership status. Read only.",
                max_results=100,
                limit=100,
            ),
            "SLACK_LIST_CHANNELS",
        )
        channels = _normalize_channels(extract_payload(channel_response))
        if not channels:
            return []

        message_tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("slack",),
            preferred=("message", "history", "list", "conversation"),
            avoid=MUTATING_KEYWORDS,
            error_label="Slack message history",
            use_case="Fetch recent Slack message history from a channel or conversation",
        )
        summaries: list[dict[str, Any]] = []
        for channel in channels:
            response = await call_selected_tool(
                self.mcp_manager,
                proc,
                message_tool,
                args_for_tool(
                    message_tool,
                    f"Fetch the last 20 Slack messages from {channel['name']} ({channel['id']}). Include sender, timestamp, and text. Read only.",
                    channel=channel["id"] or channel["name"],
                    max_results=20,
                    limit=20,
                ),
                "SLACK_FETCH_MESSAGES",
            )
            messages = _normalize_messages(extract_payload(response))[:20]
            if messages:
                summaries.append(
                    {
                        "channel": channel["name"],
                        "messages": messages,
                        "needs_response": False,
                    }
                )

        return await _group_with_mini(summaries)

    async def send_message(self, channel: str, content: str) -> dict[str, Any]:
        preview = _approval_preview(channel, content)
        approved = await self._request_approval(preview, "post_slack")
        if not approved:
            return {
                "success": False,
                "cancelled": True,
                "channel": channel,
                "content": content,
                "preview": preview,
                "message": "Slack message cancelled.",
            }

        proc = await self.mcp_manager.get_or_spawn("slack")
        tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("slack",),
            preferred=("send", "message", "post"),
            avoid=("delete", "remove"),
            error_label="Slack send message",
            use_case="Post or send a Slack message to a channel or direct-message recipient",
        )
        response = await call_selected_tool(
            self.mcp_manager,
            proc,
            tool,
            args_for_tool(
                tool,
                f"Post this Slack message to {channel}:\n\n{content}",
                channel=channel,
                recipient=channel,
                content=content,
                body=content,
            ),
            "SLACK_SEND_MESSAGE",
        )
        return {
            "success": True,
            "task": "send_slack_message",
            "channel": channel,
            "content": content,
            "preview": preview,
            "post_result": extract_payload(response),
        }

    async def _request_approval(self, preview: str, action_type: str) -> bool:
        if self.state_machine is None:
            raise RuntimeError("Approval requires StateMachine.")
        response = await self.state_machine.request_approval(
            call_id=f"slack-{uuid.uuid4()}",
            preview=preview,
            action_type=action_type,
            timeout=60.0,
        )
        return response is not None and bool(response.get("approved"))


async def read_messages(mcp_manager: MCPManager) -> list[dict[str, Any]]:
    return await SlackAgent(mcp_manager).read_messages()


async def send_message(
    mcp_manager: MCPManager,
    state_machine: StateMachine,
    channel: str,
    content: str,
) -> dict[str, Any]:
    return await SlackAgent(mcp_manager, state_machine).send_message(channel, content)


async def _group_with_mini(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not channels:
        return []

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
                    "Group Slack messages by channel for a voice assistant. "
                    "Flag needs_response=true only when a message directly asks the user for action, approval, answer, or follow-up. "
                    "Return JSON only: {\"channels\":[{\"channel\":string,\"messages\":[{\"sender\":string,\"text\":string,\"timestamp\":string}],\"needs_response\":boolean}]}"
                ),
            },
            {"role": "user", "content": json.dumps({"channels": channels}, ensure_ascii=False)},
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

    parsed = json.loads(response.json()["choices"][0]["message"]["content"])
    grouped = parsed.get("channels", [])
    if not isinstance(grouped, list):
        return channels
    return [_normalize_summary(item) for item in grouped if isinstance(item, dict)]


def _normalize_channels(payload: Any) -> list[dict[str, str]]:
    payload = coerce_json(payload)
    if isinstance(payload, dict):
        items = payload.get("channels") or payload.get("conversations") or payload.get("items") or payload.get("data") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    channels: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        is_member = item.get("is_member")
        if is_member is False:
            continue
        channel_id = str(item.get("id") or item.get("channel_id") or item.get("channelId") or "")
        name = str(item.get("name") or item.get("channel") or item.get("user") or channel_id)
        if not name:
            continue
        if not name.startswith("#") and not item.get("is_im"):
            display_name = f"#{name}"
        else:
            display_name = name
        channels.append({"id": channel_id, "name": display_name})
    return channels


def _normalize_messages(payload: Any) -> list[dict[str, str]]:
    payload = coerce_json(payload)
    if isinstance(payload, dict):
        items = payload.get("messages") or payload.get("items") or payload.get("data") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    messages: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = _clean_text(str(item.get("text") or item.get("message") or item.get("content") or ""))
        if not text:
            continue
        messages.append(
            {
                "sender": str(item.get("sender") or item.get("user") or item.get("username") or "unknown"),
                "text": text,
                "timestamp": str(item.get("timestamp") or item.get("ts") or item.get("datetime") or ""),
            }
        )
    return messages


def _normalize_summary(item: dict[str, Any]) -> dict[str, Any]:
    raw_messages = item.get("messages", [])
    messages = raw_messages if isinstance(raw_messages, list) else []
    return {
        "channel": str(item.get("channel", "unknown")),
        "messages": [
            {
                "sender": str(message.get("sender", "unknown")),
                "text": str(message.get("text", "")),
                "timestamp": str(message.get("timestamp", "")),
            }
            for message in messages
            if isinstance(message, dict)
        ],
        "needs_response": bool(item.get("needs_response")),
    }


def _approval_preview(channel: str, content: str) -> str:
    normalized = channel if channel.startswith("#") or channel.startswith("@") else f"#{channel}"
    snippet = content.strip().replace("\n", " ")[:100]
    suffix = "..." if len(content.strip()) > 100 else ""
    return f"Post to {normalized}: {snippet}{suffix}"


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
