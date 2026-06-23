import json
import re
import uuid
from typing import Any

import httpx

import urgency
from agents.composio_tools import args_for_tool, call_selected_tool, extract_payload, select_tool
from mcp_manager import MCPManager
from secrets import get as get_secret
from state import StateMachine

MUTATING_KEYWORDS = ("send", "create", "update", "delete", "remove", "draft", "reply", "trash")
SEND_KEYWORDS = ("gmail", "send", "email")
REPLY_KEYWORDS = ("gmail", "reply", "thread")
GMAIL_META_TOOLS = {"COMPOSIO_SEARCH_TOOLS", "COMPOSIO_MULTI_EXECUTE_TOOL", "COMPOSIO_EXECUTE_TOOL"}


class EmailAgent:
    def __init__(self, mcp_manager: MCPManager, state_machine: StateMachine | None = None) -> None:
        self.mcp_manager = mcp_manager
        self.state_machine = state_machine

    async def check_access(self) -> dict[str, Any]:
        proc = await self.mcp_manager.get_or_spawn("email")
        tools = await self.mcp_manager.list_tools(proc)
        gmail_tools = [tool for tool in tools if _is_gmail_tool(tool)]
        meta_tools = [tool for tool in tools if str(tool.get("name", "")) in GMAIL_META_TOOLS]
        if not gmail_tools and not meta_tools:
            return {
                "success": False,
                "has_access": False,
                "tool_count": 0,
                "message": "Gmail tools are not available. Authenticate the Gmail toolkit through Composio and retry.",
            }

        if not gmail_tools:
            try:
                await select_tool(
                    self.mcp_manager,
                    proc,
                    required=("gmail",),
                    preferred=("message", "list", "get"),
                    avoid=MUTATING_KEYWORDS,
                    error_label="Gmail access check",
                    use_case="Check whether Gmail read tools are available for the connected user",
                )
            except RuntimeError as exc:
                return {
                    "success": False,
                    "has_access": False,
                    "tool_count": len(tools),
                    "message": str(exc),
                }

        return {
            "success": True,
            "has_access": True,
            "tool_count": len(gmail_tools) or len(tools),
            "message": "Gmail is connected and available.",
        }

    async def list_emails(self, query: str = "in:inbox", max_results: int = 25) -> dict[str, Any]:
        query = query.strip() or "in:inbox"
        max_results = _bounded_max_results(max_results)
        proc = await self.mcp_manager.get_or_spawn("email")
        tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("gmail",),
            preferred=("email", "message", "list", "search"),
            avoid=MUTATING_KEYWORDS,
            error_label="Gmail email list",
            use_case="List Gmail emails matching a Gmail query with id thread id sender subject date and snippet",
        )
        response = await call_selected_tool(
            self.mcp_manager,
            proc,
            tool,
            args_for_tool(
                tool,
                f"List up to {max_results} Gmail emails matching query: {query}. Include id, thread id, sender/from, subject, date, and body snippet. Read only.",
                max_results=max_results,
                query=query,
            ),
            "GMAIL_LIST_EMAILS",
        )
        return {
            "success": True,
            "task": "list_emails",
            "query": query,
            "emails": _normalize_emails(extract_payload(response)),
        }

    async def search_emails(self, query: str, max_results: int = 10) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("Gmail search requires a query.")
        result = await self.list_emails(query=query, max_results=max_results)
        result["task"] = "search_emails"
        return result

    async def read_email(self, identifier: str) -> dict[str, Any]:
        identifier = _clean_identifier(identifier)
        prefer_direct_fetch = False
        if not identifier:
            latest = await self.list_emails(query="in:inbox", max_results=1)
            emails = latest.get("emails", [])
            if not emails:
                return {
                    "success": True,
                    "task": "read_email",
                    "thread_id": "",
                    "messages": [],
                    "message": "No inbox emails were found.",
                }
            first_email = emails[0]
            identifier = first_email.get("thread_id") or first_email.get("id") or first_email.get("subject", "")
            prefer_direct_fetch = bool(first_email.get("thread_id") or first_email.get("id"))

        thread = await self._fetch_thread(identifier, prefer_direct_fetch=prefer_direct_fetch)
        return {
            "success": True,
            "task": "read_email",
            "identifier": identifier,
            "thread_id": thread["thread_id"],
            "messages": thread["messages"],
        }

    async def triage_inbox(self) -> dict[str, list[dict[str, str]]]:
        proc = await self.mcp_manager.get_or_spawn("email")
        tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("gmail",),
            preferred=("unread", "email", "message", "list"),
            avoid=MUTATING_KEYWORDS,
            error_label="Gmail unread message list",
            use_case="List up to 25 unread Gmail emails with sender subject thread id and snippet",
        )
        response = await call_selected_tool(
            self.mcp_manager,
            proc,
            tool,
            args_for_tool(
                tool,
                "List up to 25 unread Gmail emails. Include id, thread id, sender/from, subject, and body snippet. Read only.",
                max_results=25,
                query="is:unread",
            ),
            "GMAIL_LIST_UNREAD",
        )
        emails = _normalize_emails(extract_payload(response))
        classifications = await urgency.classify(emails)

        buckets: dict[str, list[dict[str, str]]] = {
            "high": [],
            "medium": [],
            "low": [],
        }
        for item in classifications:
            email = item["email"]
            buckets[item["urgency"]].append(
                {
                    "id": email.get("id", ""),
                    "thread_id": email.get("thread_id", ""),
                    "sender": email.get("sender", "unknown sender"),
                    "subject": email.get("subject", "(no subject)"),
                    "preview": email.get("body_preview", ""),
                    "reason": item.get("reason", ""),
                }
            )
        return buckets

    async def summarize_thread(self, identifier: str) -> dict[str, str]:
        thread = await self._fetch_thread(identifier)
        summary = await _summarize_with_gpt55(thread["messages"])
        return {
            "identifier": identifier,
            "thread_id": thread["thread_id"],
            "summary": summary,
        }

    async def draft_and_send(self, dictated_content: dict[str, str]) -> dict[str, Any]:
        draft = await _polish_email_with_gpt55(dictated_content)
        return await self._send_approved_email(draft, task_name="draft_and_send")

    async def send_email(self, recipient: str, subject: str, body: str) -> dict[str, Any]:
        draft = {
            "recipient": recipient.strip(),
            "subject": subject.strip(),
            "body": body.strip(),
        }
        return await self._send_approved_email(draft, task_name="send_email")

    async def _send_approved_email(self, draft: dict[str, str], task_name: str) -> dict[str, Any]:
        if not draft["recipient"]:
            raise ValueError("Email recipient is required.")
        if not draft["body"]:
            raise ValueError("Email body is required.")

        preview = _approval_preview(draft["recipient"], draft["subject"], draft["body"])
        approved = await self._request_approval(preview, "send_email")
        if not approved:
            return {
                "success": False,
                "cancelled": True,
                "draft": draft,
                "message": "Email send cancelled.",
            }

        proc = await self.mcp_manager.get_or_spawn("email")
        tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("gmail",),
            preferred=SEND_KEYWORDS,
            avoid=("delete", "trash", "remove"),
            error_label="Gmail send email",
            use_case="Send a Gmail email to a recipient with subject and body",
        )
        response = await call_selected_tool(
            self.mcp_manager,
            proc,
            tool,
            args_for_tool(
                tool,
                f"Send an email to {draft['recipient']} with subject {draft['subject']} and body below.\n\n{draft['body']}",
                recipient=draft["recipient"],
                to=draft["recipient"],
                subject=draft["subject"],
                body=draft["body"],
            ),
            "GMAIL_SEND_EMAIL",
        )
        return {
            "success": True,
            "task": task_name,
            "draft": draft,
            "send_result": extract_payload(response),
        }

    async def reply_to_thread(self, identifier: str, dictated_intent: str) -> dict[str, Any]:
        thread = await self._fetch_thread(identifier)
        draft = await _draft_reply_with_gpt55(thread["messages"], dictated_intent)
        recipient = draft.get("recipient") or _last_sender(thread["messages"])
        subject = draft.get("subject") or _reply_subject(thread["messages"])
        preview = _approval_preview(recipient, subject, draft["body"])
        approved = await self._request_approval(preview, "reply_email")
        if not approved:
            return {
                "success": False,
                "cancelled": True,
                "draft": {"recipient": recipient, "subject": subject, "body": draft["body"]},
                "message": "Email reply cancelled.",
            }

        proc = await self.mcp_manager.get_or_spawn("email")
        tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("gmail",),
            preferred=REPLY_KEYWORDS,
            avoid=("delete", "trash", "remove"),
            error_label="Gmail reply to thread",
            use_case="Reply to an existing Gmail thread with a message body",
        )
        response = await call_selected_tool(
            self.mcp_manager,
            proc,
            tool,
            args_for_tool(
                tool,
                f"Reply to Gmail thread {thread['thread_id']} with this body.\n\n{draft['body']}",
                thread_id=thread["thread_id"],
                recipient=recipient,
                to=recipient,
                subject=subject,
                body=draft["body"],
            ),
            "GMAIL_REPLY_THREAD",
        )
        return {
            "success": True,
            "task": "reply_to_thread",
            "thread_id": thread["thread_id"],
            "draft": {"recipient": recipient, "subject": subject, "body": draft["body"]},
            "send_result": extract_payload(response),
        }

    async def _fetch_thread(self, identifier: str, prefer_direct_fetch: bool = False) -> dict[str, Any]:
        identifier = _clean_identifier(identifier)
        if not identifier:
            inbox = await self.triage_inbox()
            messages = [email for bucket in inbox.values() for email in bucket]
            return {"thread_id": "inbox", "messages": messages}

        proc = await self.mcp_manager.get_or_spawn("email")
        if prefer_direct_fetch:
            try:
                direct_thread = await self._fetch_thread_by_id(proc, identifier)
                if direct_thread["messages"]:
                    return direct_thread
            except RuntimeError:
                pass

        search_tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("gmail",),
            preferred=("thread", "search", "email", "message"),
            avoid=MUTATING_KEYWORDS,
            error_label="Gmail thread search",
            use_case="Search Gmail threads by sender name or subject and return thread id and messages",
        )
        search_response = await call_selected_tool(
            self.mcp_manager,
            proc,
            search_tool,
            args_for_tool(
                search_tool,
                f"Find the Gmail thread matching sender name or subject: {identifier}. Include thread id and all message ids if available. Read only.",
                max_results=5,
                query=identifier,
            ),
            "GMAIL_SEARCH_THREADS",
        )
        payload = extract_payload(search_response)
        messages = _normalize_thread_messages(payload)

        thread_id = _best_thread_id(payload)
        if thread_id and not messages:
            fetched_thread = await self._fetch_thread_by_id(proc, thread_id)
            payload = fetched_thread["payload"]
            messages = _normalize_thread_messages(payload)

        if not messages:
            return {
                "thread_id": thread_id or "search",
                "messages": [
                    {
                        "sender": "Gmail",
                        "sent_at": "",
                        "subject": f"Search results for {identifier}",
                        "body": _clean_body(json.dumps(payload, ensure_ascii=False)[:4000]),
                    }
                ],
            }

        return {"thread_id": thread_id or "search", "messages": messages}

    async def _fetch_thread_by_id(self, proc: Any, thread_id: str) -> dict[str, Any]:
        get_tool = await select_tool(
            self.mcp_manager,
            proc,
            required=("gmail",),
            preferred=("thread", "get", "fetch", "message"),
            avoid=MUTATING_KEYWORDS,
            error_label="Gmail fetch thread",
            use_case="Fetch a full Gmail thread by thread id with all messages sender date subject and body",
        )
        thread_response = await call_selected_tool(
            self.mcp_manager,
            proc,
            get_tool,
            args_for_tool(
                get_tool,
                f"Fetch the full Gmail thread {thread_id}. Include all messages with sender, date, subject, and body. Read only.",
                thread_id=thread_id,
            ),
            "GMAIL_FETCH_THREAD",
        )
        payload = extract_payload(thread_response)
        return {
            "thread_id": thread_id,
            "messages": _normalize_thread_messages(payload),
            "payload": payload,
        }

    async def _request_approval(self, preview: str, action_type: str) -> bool:
        if self.state_machine is None:
            raise RuntimeError("Approval requires StateMachine.")
        response = await self.state_machine.request_approval(
            call_id=f"email-{uuid.uuid4()}",
            preview=preview,
            action_type=action_type,
            timeout=60.0,
        )
        return response is not None and bool(response.get("approved"))


async def check_access(mcp_manager: MCPManager) -> dict[str, Any]:
    return await EmailAgent(mcp_manager).check_access()


async def list_emails(
    mcp_manager: MCPManager,
    query: str = "in:inbox",
    max_results: int = 25,
) -> dict[str, Any]:
    return await EmailAgent(mcp_manager).list_emails(query=query, max_results=max_results)


async def search_emails(
    mcp_manager: MCPManager,
    query: str,
    max_results: int = 10,
) -> dict[str, Any]:
    return await EmailAgent(mcp_manager).search_emails(query=query, max_results=max_results)


async def read_email(mcp_manager: MCPManager, identifier: str) -> dict[str, Any]:
    return await EmailAgent(mcp_manager).read_email(identifier)


async def triage_inbox(mcp_manager: MCPManager) -> dict[str, list[dict[str, str]]]:
    return await EmailAgent(mcp_manager).triage_inbox()


async def summarize_thread(
    mcp_manager: MCPManager,
    identifier: str,
) -> dict[str, str]:
    return await EmailAgent(mcp_manager).summarize_thread(identifier)


async def draft_and_send(
    mcp_manager: MCPManager,
    state_machine: StateMachine,
    dictated_content: dict[str, str],
) -> dict[str, Any]:
    return await EmailAgent(mcp_manager, state_machine).draft_and_send(dictated_content)


async def send_email(
    mcp_manager: MCPManager,
    state_machine: StateMachine,
    recipient: str,
    subject: str,
    body: str,
) -> dict[str, Any]:
    return await EmailAgent(mcp_manager, state_machine).send_email(recipient, subject, body)


async def reply_to_thread(
    mcp_manager: MCPManager,
    state_machine: StateMachine,
    identifier: str,
    dictated_intent: str,
) -> dict[str, Any]:
    return await EmailAgent(mcp_manager, state_machine).reply_to_thread(identifier, dictated_intent)


async def _summarize_with_gpt55(messages: list[dict[str, str]]) -> str:
    return await _chat_gpt55(
        "Summarize the email thread in one concise paragraph for a voice assistant. Return only the summary.",
        {"messages": messages},
        response_format=None,
    )


async def _polish_email_with_gpt55(content: dict[str, str]) -> dict[str, str]:
    response = await _chat_gpt55(
        "Polish this dictated email into a clear, professional email. Return JSON only: {\"recipient\": string, \"subject\": string, \"body\": string}.",
        content,
        response_format={"type": "json_object"},
    )
    parsed = json.loads(response)
    return {
        "recipient": str(parsed.get("recipient") or content.get("recipient") or ""),
        "subject": str(parsed.get("subject") or content.get("subject") or ""),
        "body": str(parsed.get("body") or content.get("body") or ""),
    }


async def _draft_reply_with_gpt55(messages: list[dict[str, str]], dictated_intent: str) -> dict[str, str]:
    response = await _chat_gpt55(
        "Draft a concise reply to this email thread based on the dictated intent. Return JSON only: {\"recipient\": string, \"subject\": string, \"body\": string}.",
        {"thread": messages, "dictated_intent": dictated_intent},
        response_format={"type": "json_object"},
    )
    parsed = json.loads(response)
    return {
        "recipient": str(parsed.get("recipient", "")),
        "subject": str(parsed.get("subject", "")),
        "body": str(parsed.get("body", "")),
    }


async def _chat_gpt55(
    system_prompt: str,
    payload_data: dict[str, Any],
    response_format: dict[str, str] | None,
) -> str:
    endpoint = get_secret("azure_gpt55_endpoint").rstrip("/")
    api_key = get_secret("azure_gpt55_key")
    deployment = get_secret("azure_gpt55_deployment")
    url = f"{endpoint}/openai/v1/chat/completions"

    payload: dict[str, Any] = {
        "model": deployment,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(payload_data, ensure_ascii=False)},
        ],
        "temperature": 0.2,
    }
    if response_format is not None:
        payload["response_format"] = response_format

    async with httpx.AsyncClient(timeout=45.0) as client:
        response = await client.post(
            url,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()

    return str(response.json()["choices"][0]["message"]["content"]).strip()


def _is_gmail_tool(tool: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(tool.get(key, ""))
        for key in ("name", "title", "description")
    ).lower()
    return "gmail" in haystack


def _bounded_max_results(value: int) -> int:
    try:
        normalized_value = int(value)
    except (TypeError, ValueError):
        normalized_value = 25
    return min(100, max(1, normalized_value))


def _clean_identifier(identifier: str) -> str:
    cleaned = identifier.strip().strip('"\'')
    lowered = cleaned.lower()
    empty_requests = (
        "do you have access to my gmail",
        "can you access my gmail",
        "check gmail access",
        "gmail access",
        "my gmail",
        "gmail",
        "email",
        "emails",
        "inbox",
    )
    if lowered in empty_requests:
        return ""
    for prefix in (
        "summarize email thread",
        "summarize thread",
        "summarize email",
        "summarize",
        "summary of email thread",
        "summary of thread",
        "summary of",
    ):
        if lowered.startswith(prefix):
            return cleaned[len(prefix) :].strip(" :.-")
    return cleaned


def _approval_preview(recipient: str, subject: str, body: str) -> str:
    snippet = body.strip().replace("\n", " ")[:100]
    suffix = "..." if len(body.strip()) > 100 else ""
    return f"Send email to {recipient} — Subject: {subject}\n{snippet}{suffix}"


def _last_sender(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        sender = message.get("sender", "")
        if sender:
            return sender
    return "unknown recipient"


def _reply_subject(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        subject = message.get("subject", "")
        if subject:
            return subject if subject.lower().startswith("re:") else f"Re: {subject}"
    return "Re:"


def _normalize_emails(payload: Any) -> list[dict[str, str]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return []

    if isinstance(payload, dict):
        items = payload.get("emails") or payload.get("messages") or payload.get("items") or payload.get("data")
        if items is None and any(key in payload for key in ("id", "thread_id", "threadId", "subject", "snippet")):
            items = [payload]
        items = items or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    emails: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        body = str(
            item.get("body_preview")
            or item.get("snippet")
            or item.get("body")
            or item.get("text")
            or ""
        )
        emails.append(
            {
                "id": str(item.get("id", "")),
                "thread_id": str(item.get("thread_id") or item.get("threadId") or ""),
                "subject": str(item.get("subject", "(no subject)")),
                "sender": str(item.get("sender") or item.get("from") or "unknown sender"),
                "sent_at": str(item.get("sent_at") or item.get("date") or item.get("internalDate") or ""),
                "body_preview": body[:200],
            }
        )
    return emails


def _extract_thread_id(payload: Any, identifier: str) -> str:
    thread_id = _best_thread_id(payload)
    if thread_id:
        return thread_id
    raise ValueError(f"No email thread found for: {identifier}")


def _best_thread_id(payload: Any) -> str:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return ""
    if isinstance(payload, dict):
        if payload.get("thread_id") or payload.get("threadId") or payload.get("id"):
            return str(payload.get("thread_id") or payload.get("threadId") or payload.get("id"))
        threads = payload.get("threads") or payload.get("items") or payload.get("messages") or payload.get("data") or []
    elif isinstance(payload, list):
        threads = payload
    else:
        threads = []

    for thread in threads:
        if not isinstance(thread, dict):
            continue
        thread_id = thread.get("thread_id") or thread.get("threadId") or thread.get("id")
        if thread_id:
            return str(thread_id)
    return ""


def _normalize_thread_messages(payload: Any) -> list[dict[str, str]]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return [{"sender": "unknown sender", "sent_at": "", "subject": "", "body": _clean_body(payload)}]

    if isinstance(payload, dict):
        items = payload.get("messages") or payload.get("emails") or payload.get("items") or payload.get("data")
        if items is None and any(key in payload for key in ("id", "thread_id", "threadId", "subject", "body", "snippet")):
            items = [payload]
        items = items or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    messages: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        messages.append(
            {
                "sender": str(item.get("sender") or item.get("from") or "unknown sender"),
                "sent_at": str(item.get("sent_at") or item.get("date") or item.get("internalDate") or ""),
                "subject": str(item.get("subject", "(no subject)")),
                "body": _clean_body(
                    str(
                        item.get("body")
                        or item.get("text")
                        or item.get("snippet")
                        or item.get("body_preview")
                        or ""
                    )
                ),
            }
        )
    return messages


def _clean_body(body: str) -> str:
    return re.sub(r"\s+", " ", body).strip()
