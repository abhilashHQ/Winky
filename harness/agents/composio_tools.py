import json
from typing import Any

from mcp_manager import MCPManager, MCPProcess

META_SEARCH_TOOL = "COMPOSIO_SEARCH_TOOLS"
META_EXECUTE_TOOL = "COMPOSIO_MULTI_EXECUTE_TOOL"
READONLY_KEYWORDS = ("get", "list", "search", "fetch", "read", "retrieve")
MUTATING_KEYWORDS = ("send", "create", "update", "delete", "remove", "post", "draft", "reply", "trash")


def tool_text(tool: dict[str, Any]) -> str:
    return " ".join(
        str(part)
        for part in (
            tool.get("name", ""),
            tool.get("title", ""),
            tool.get("description", ""),
        )
    ).lower()


async def select_tool(
    mcp_manager: MCPManager,
    proc: MCPProcess,
    required: tuple[str, ...],
    preferred: tuple[str, ...],
    avoid: tuple[str, ...] = (),
    error_label: str = "Composio",
    use_case: str | None = None,
) -> dict[str, Any]:
    tools = await mcp_manager.list_tools(proc)
    candidates: list[tuple[int, dict[str, Any]]] = []
    for tool in tools:
        haystack = tool_text(tool)
        if any(word in haystack for word in avoid):
            continue
        if required and not all(word in haystack for word in required):
            continue
        score = sum(3 for word in preferred if word in haystack)
        score += sum(1 for word in READONLY_KEYWORDS if word in haystack)
        candidates.append((score, tool))

    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    if use_case:
        return await search_tool(mcp_manager, proc, use_case, preferred, error_label)

    raise RuntimeError(
        f"No matching {error_label} tool was found in Composio Connect. "
        f"Authenticate the toolkit through Composio and retry."
    )


async def search_tool(
    mcp_manager: MCPManager,
    proc: MCPProcess,
    use_case: str,
    preferred: tuple[str, ...],
    error_label: str,
) -> dict[str, Any]:
    response = await mcp_manager.call_tool(
        proc,
        META_SEARCH_TOOL,
        {
            "queries": [{"use_case": use_case}],
            "session": {"generate_id": True},
            "model": "gpt-4o-mini",
        },
    )
    payload = coerce_json(extract_payload(response))
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    tool_schemas = data.get("tool_schemas", {}) if isinstance(data, dict) else {}
    results = data.get("results", []) if isinstance(data, dict) else []
    slugs: list[str] = []
    for result in results if isinstance(results, list) else []:
        if not isinstance(result, dict):
            continue
        for key in ("primary_tool_slugs", "related_tool_slugs"):
            values = result.get(key, [])
            if isinstance(values, list):
                slugs.extend(str(value) for value in values)

    scored: list[tuple[int, str]] = []
    for slug in dict.fromkeys(slugs):
        lowered = slug.lower()
        score = sum(3 for word in preferred if word in lowered)
        scored.append((score, slug))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        raise RuntimeError(f"No {error_label} tool found through COMPOSIO_SEARCH_TOOLS.")

    slug = scored[0][1]
    schema = tool_schemas.get(slug, {}) if isinstance(tool_schemas, dict) else {}
    if not isinstance(schema, dict):
        schema = {}
    return {
        "name": META_EXECUTE_TOOL,
        "slug": slug,
        "inputSchema": schema.get("input_schema") or schema.get("inputSchema") or {},
        "description": schema.get("description", ""),
    }


async def call_selected_tool(
    mcp_manager: MCPManager,
    proc: MCPProcess,
    tool: dict[str, Any],
    arguments: dict[str, Any],
    current_step: str,
) -> dict[str, Any]:
    if tool.get("name") != META_EXECUTE_TOOL:
        return await mcp_manager.call_tool(proc, str(tool["name"]), arguments)

    response = await mcp_manager.call_tool(
        proc,
        META_EXECUTE_TOOL,
        {
            "tools": [{"tool_slug": str(tool["slug"]), "arguments": arguments}],
            "thought": f"Execute {current_step.lower().replace('_', ' ')}.",
            "sync_response_to_workbench": False,
            "current_step": current_step,
            "current_step_metric": "1/1 tools",
        },
    )
    payload = coerce_json(extract_payload(response))
    if isinstance(payload, dict):
        data = payload.get("data", payload)
        results = data.get("results", []) if isinstance(data, dict) else []
        if results and isinstance(results[0], dict):
            first = results[0]
            if first.get("error"):
                raise RuntimeError(str(first["error"]))
            return {"result": first.get("response", first)}
    return response


def args_for_tool(tool: dict[str, Any], instruction: str, **values: Any) -> dict[str, Any]:
    schema = tool.get("inputSchema") or tool.get("input_schema") or {}
    properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    args: dict[str, Any] = {}

    for key, raw_schema in properties.items():
        lowered = key.lower()
        field_schema = raw_schema if isinstance(raw_schema, dict) else {}
        field_type = field_schema.get("type")

        if lowered in {"query", "q", "search", "search_query"}:
            args[key] = values.get("query") or instruction
        elif lowered in {"prompt", "instruction", "instructions", "request", "use_case"}:
            args[key] = instruction
        elif lowered in {"max_results", "maxresults", "limit", "count", "page_size", "pagesize"}:
            args[key] = values.get("max_results") or values.get("limit") or 20
        elif lowered in {"channel", "channel_id", "channelid"} and values.get("channel"):
            args[key] = values["channel"]
        elif lowered in {"user", "user_id", "userid", "recipient"} and values.get("recipient"):
            args[key] = values["recipient"]
        elif lowered in {"to", "email", "to_email", "attendee", "attendees"} and values.get("attendee"):
            if field_type == "array" or lowered in {"attendees"}:
                args[key] = [values["attendee"]]
            else:
                args[key] = values["attendee"]
        elif lowered in {"recipient", "to", "email", "to_email"} and values.get("recipient"):
            args[key] = values["recipient"]
        elif lowered in {"subject", "title", "summary"} and values.get("title"):
            args[key] = values["title"]
        elif lowered in {"subject", "title", "summary"} and values.get("subject"):
            args[key] = values["subject"]
        elif lowered in {"body", "message", "content", "text"} and values.get("content"):
            args[key] = values["content"]
        elif lowered in {"body", "message", "content", "text"} and values.get("body"):
            args[key] = values["body"]
        elif lowered in {"thread_id", "threadid"} and values.get("thread_id"):
            args[key] = values["thread_id"]
        elif lowered in {"id"} and values.get("thread_id"):
            args[key] = values["thread_id"]
        elif lowered in {"event_id", "eventid", "id"} and values.get("event_id"):
            args[key] = values["event_id"]
        elif lowered in {"start", "start_time", "starttime", "time_min", "timemin"} and values.get("start"):
            args[key] = values["start"]
        elif lowered in {"end", "end_time", "endtime", "time_max", "timemax"} and values.get("end"):
            args[key] = values["end"]
        elif lowered in {"date"} and values.get("date"):
            args[key] = values["date"]
        elif lowered in {"timezone", "time_zone", "tz"}:
            args[key] = values.get("timezone", "local")
        elif key in values and values[key] is not None:
            args[key] = values[key]
        elif key in required:
            args[key] = values.get(key, instruction)

    if args:
        return args

    fallback = {key: value for key, value in values.items() if value is not None}
    fallback.setdefault("query", values.get("query") or instruction)
    return fallback


def extract_payload(response: dict[str, Any]) -> Any:
    if "result" in response:
        return extract_payload(response["result"])
    if "structuredContent" in response:
        return response["structuredContent"]
    if "structured_content" in response:
        return response["structured_content"]
    if "content" in response:
        content = response["content"]
        if isinstance(content, list):
            text_parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            text = "\n".join(text_parts).strip()
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
        return content
    return response


def coerce_json(payload: Any) -> Any:
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload
    return payload
