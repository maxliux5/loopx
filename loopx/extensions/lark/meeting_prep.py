"""Bounded, user-authenticated Lark inputs for meeting preparation."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timedelta
import json
from pathlib import Path
import re
import subprocess
from typing import Any


CommandRunner = Callable[[list[str], Path | None, float | None], dict[str, object]]
_DOC_LINK_RE = re.compile(r"https?://[^\s)\]>]+/(?:docx|wiki)/[A-Za-z0-9_-]+")


def default_target_date() -> str:
    return (date.today() + timedelta(days=1)).isoformat()


def _agenda_command(target_date: str, *, cli_bin: str) -> list[str]:
    return [
        cli_bin,
        "--as",
        "user",
        "calendar",
        "+agenda",
        "--start",
        target_date,
        "--end",
        target_date,
        "--format",
        "json",
    ]


def build_lark_meeting_prep_plan(
    *,
    target_date: str,
    chat_ids: Iterable[str] = (),
    docs: Iterable[str] = (),
    cli_bin: str = "lark-cli",
) -> dict[str, object]:
    """Describe the exact read boundary without contacting Lark."""
    normalized_chats = _unique_nonempty(chat_ids)
    normalized_docs = _unique_nonempty(docs)
    commands = [_agenda_command(target_date, cli_bin=cli_bin)]
    for doc in normalized_docs:
        commands.extend(_document_plan_commands(doc, cli_bin=cli_bin))
    chat_start = _chat_start_date(target_date)
    commands.extend(
        [
            cli_bin,
            "--as",
            "user",
            "im",
            "+chat-messages-list",
            "--chat-id",
            chat_id,
            "--start",
            chat_start,
            "--end",
            target_date,
            "--order",
            "asc",
            "--page-size",
            "50",
            "--format",
            "json",
        ]
        for chat_id in normalized_chats
    )
    return {
        "ok": True,
        "schema_version": "meeting_prep_plan_v0",
        "target_date": target_date,
        "identity": "user",
        "commands": commands,
        "automatic_sources": [
            "calendar agenda",
            "meeting notes linked by calendar event id",
            "documents linked from event descriptions",
        ],
        "explicit_sources": {
            "chat_ids": normalized_chats,
            "docs": normalized_docs,
        },
        "source_boundary": {
            "raw_storage": "project_local_ignored",
            "projection": "bounded todo rows without raw source bodies",
            "credentials": "lark-cli auth only",
        },
    }


def prepare_lark_meetings(
    *,
    target_date: str,
    agenda_fixture: Path | None = None,
    chat_ids: Iterable[str] = (),
    docs: Iterable[str] = (),
    event_ids: Iterable[str] = (),
    execute: bool = False,
    cli_bin: str = "lark-cli",
    runner: CommandRunner | None = None,
    private_workdir: Path | None = None,
) -> dict[str, object]:
    """Read bounded meeting sources and produce a local preparation packet."""
    if agenda_fixture is None and not execute:
        raise ValueError("prepare requires --execute or an agenda fixture")
    command_runner = runner or default_subprocess_runner
    connector_workdir = (
        private_workdir
        or Path.cwd() / ".loopx" / "meeting-prep" / target_date / "connector-runtime"
    ).resolve()
    if execute:
        connector_workdir.mkdir(parents=True, exist_ok=True)
    if agenda_fixture is not None:
        agenda_payload = _read_json_object(agenda_fixture)
    else:
        agenda_payload = _run_json(
            _agenda_command(target_date, cli_bin=cli_bin),
            runner=command_runner,
            cwd=connector_workdir,
        )
    raw_events = agenda_payload.get("data")
    if not isinstance(raw_events, list):
        raise ValueError("calendar agenda response requires a data array")

    selected_ids = set(_unique_nonempty(event_ids))
    events = [
        event
        for event in raw_events
        if isinstance(event, dict)
        and (not selected_ids or str(event.get("event_id") or "") in selected_ids)
    ]
    explicit_docs = _unique_nonempty(docs)
    explicit_chats = _unique_nonempty(chat_ids)
    if (explicit_docs or explicit_chats) and len(events) != 1:
        raise ValueError(
            "explicit --doc/--chat-id sources require exactly one selected meeting; use --event-id"
        )
    linked_docs = _unique_nonempty(
        source
        for event in events
        for source in _document_sources_from_event(event)
    )
    all_docs = _unique_nonempty((*explicit_docs, *linked_docs))
    source_reads: dict[str, object] = {
        "calendar": {"status": "read", "event_count": len(events)},
        "meeting_notes": _meeting_notes_read(
            events,
            execute=execute,
            cli_bin=cli_bin,
            runner=command_runner,
            cwd=connector_workdir,
        ),
        "documents": _documents_read(
            all_docs,
            execute=execute,
            cli_bin=cli_bin,
            runner=command_runner,
            cwd=connector_workdir,
        ),
        "chat": _chats_read(
            explicit_chats,
            target_date=target_date,
            execute=execute,
            cli_bin=cli_bin,
            runner=command_runner,
            cwd=connector_workdir,
        ),
    }

    meetings = []
    for event in sorted(events, key=_event_sort_key):
        event_docs = _unique_nonempty((*explicit_docs, *_document_sources_from_event(event)))
        meetings.append(
            _meeting_packet(
                event,
                source_reads=source_reads,
                event_docs=event_docs,
                has_chats=bool(explicit_chats),
            )
        )
    todo_projection = [_todo_projection(item) for item in meetings]
    return {
        "ok": True,
        "schema_version": "meeting_prep_packet_v0",
        "goal_id": f"lark-meeting-prep:{target_date}",
        "source_id": f"lark-meeting-prep:{target_date}",
        "target_date": target_date,
        "meeting_count": len(meetings),
        "meetings": meetings,
        "source_reads": source_reads,
        "agent_todos": {"items": todo_projection},
        "todo_projection": todo_projection,
        "privacy": {
            "classification": "owner_private",
            "sink_rule": "only todo_projection may be sent to a shared display sink after redaction",
        },
    }


def render_lark_meeting_prep_markdown(payload: dict[str, object]) -> str:
    if not payload.get("ok"):
        return f"# Lark meeting preparation\n\nError: {payload.get('error', 'unknown error')}"
    if payload.get("schema_version") == "meeting_prep_plan_v0":
        lines = ["# Lark meeting preparation plan", "", f"Date: {payload.get('target_date')}", "", "Commands:"]
        for command in payload.get("commands", []):
            if isinstance(command, list):
                lines.append(f"- `{' '.join(str(part) for part in command)}`")
        return "\n".join(lines)
    lines = ["# Lark meeting preparation", "", f"Date: {payload.get('target_date')}"]
    meetings = payload.get("meetings")
    if not isinstance(meetings, list) or not meetings:
        return "\n".join([*lines, "", "No meetings found."])
    for meeting in meetings:
        if not isinstance(meeting, dict):
            continue
        lines.extend(["", f"## {meeting.get('title', 'Untitled meeting')}", ""])
        lines.append(f"- Time: {meeting.get('start')} - {meeting.get('end')}")
        for item in meeting.get("preparation_items", []):
            lines.append(f"- [ ] {item}")
    return "\n".join(lines)


def compact_lark_meeting_prep_packet(payload: dict[str, object]) -> dict[str, object]:
    """Remove raw connector bodies while retaining preparation and source status."""
    compact = {key: value for key, value in payload.items() if key != "source_reads"}
    reads = payload.get("source_reads")
    compact_reads: dict[str, object] = {}
    if isinstance(reads, dict):
        for name, value in reads.items():
            if not isinstance(value, dict):
                continue
            item: dict[str, object] = {"status": value.get("status", "unknown")}
            if "event_count" in value:
                item["event_count"] = value["event_count"]
            source_items = value.get("items")
            if isinstance(source_items, list):
                item["item_count"] = len(source_items)
                item["statuses"] = [
                    str(source.get("status") or "unknown")
                    for source in source_items
                    if isinstance(source, dict)
                ]
            if "error" in value:
                item["error"] = str(value["error"])[:300]
            compact_reads[name] = item
    compact["source_reads"] = compact_reads
    compact["details"] = "raw connector bodies are available only in --output-private"
    return compact


def default_subprocess_runner(
    args: list[str], cwd: Path | None, timeout: float | None
) -> dict[str, object]:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _run_json(
    command: list[str], *, runner: CommandRunner, cwd: Path
) -> dict[str, object]:
    forbidden = {"--ak", "--sk", "--app-secret", "--access-token"}
    if any(part in forbidden for part in command):
        raise ValueError("credentials must remain in lark-cli auth")
    result = runner(command, cwd, 60.0)
    returncode = int(result.get("returncode") or 0)
    stdout = str(result.get("stdout") or "")
    if returncode != 0:
        raise RuntimeError(_compact_error(result))
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("lark-cli returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("lark-cli JSON response must be an object")
    if payload.get("ok") is False:
        raise RuntimeError(_payload_error(payload))
    return payload


def _safe_read(
    command: list[str], *, runner: CommandRunner, cwd: Path
) -> dict[str, object]:
    try:
        payload = _run_json(command, runner=runner, cwd=cwd)
        return {"status": "read", "payload": payload}
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)[:500]}


def _meeting_notes_read(
    events: list[dict[str, Any]],
    *,
    execute: bool,
    cli_bin: str,
    runner: CommandRunner,
    cwd: Path,
) -> dict[str, object]:
    ids = _unique_nonempty(str(event.get("event_id") or "") for event in events)
    if not ids:
        return {"status": "not_available", "reason": "no calendar event ids"}
    items: list[dict[str, object]] = []
    for event_id in ids[:50]:
        command = [
            cli_bin,
            "--as",
            "user",
            "vc",
            "+notes",
            "--calendar-event-ids",
            event_id,
            "--format",
            "json",
        ]
        item: dict[str, object] = {
            "source": event_id,
            "status": "planned",
            "command": command,
        }
        if execute:
            item = {"source": event_id, **_safe_read(command, runner=runner, cwd=cwd)}
            if item.get("status") == "read":
                note_docs = []
                for token in _note_document_tokens(item.get("payload"))[:10]:
                    fetch = [
                        cli_bin,
                        "--as",
                        "user",
                        "docs",
                        "+fetch",
                        "--doc",
                        token,
                        "--format",
                        "json",
                    ]
                    note_docs.append(
                        {"source": token, **_safe_read(fetch, runner=runner, cwd=cwd)}
                    )
                item["documents"] = note_docs
        items.append(item)
    return {"status": _aggregate_item_status(items), "items": items}


def _documents_read(
    docs: list[str],
    *,
    execute: bool,
    cli_bin: str,
    runner: CommandRunner,
    cwd: Path,
) -> dict[str, object]:
    if not docs:
        return {"status": "not_configured", "items": []}
    items = []
    for doc in docs[:10]:
        commands = _document_plan_commands(doc, cli_bin=cli_bin)
        item: dict[str, object] = {"source": doc, "status": "planned", "commands": commands}
        if execute:
            item = {
                "source": doc,
                **_read_document_source(
                    doc, cli_bin=cli_bin, runner=runner, cwd=cwd
                ),
            }
        items.append(item)
    statuses = {str(item.get("status")) for item in items}
    status = "read" if statuses == {"read"} else "partial" if "read" in statuses else next(iter(statuses))
    return {"status": status, "items": items}


def _chats_read(
    chat_ids: list[str],
    *,
    target_date: str,
    execute: bool,
    cli_bin: str,
    runner: CommandRunner,
    cwd: Path,
) -> dict[str, object]:
    if not chat_ids:
        return {"status": "not_configured", "items": []}
    items = []
    chat_start = _chat_start_date(target_date)
    for chat_id in chat_ids[:10]:
        command = [
            cli_bin,
            "--as",
            "user",
            "im",
            "+chat-messages-list",
            "--chat-id",
            chat_id,
            "--start",
            chat_start,
            "--end",
            target_date,
            "--order",
            "asc",
            "--page-size",
            "50",
            "--format",
            "json",
        ]
        item = {"source": chat_id, "status": "planned", "command": command}
        if execute:
            item = {
                "source": chat_id,
                **_safe_read(command, runner=runner, cwd=cwd),
            }
        items.append(item)
    statuses = {str(item.get("status")) for item in items}
    status = "read" if statuses == {"read"} else "partial" if "read" in statuses else next(iter(statuses))
    return {"status": status, "items": items}


def _meeting_packet(
    event: dict[str, Any],
    *,
    source_reads: dict[str, object],
    event_docs: list[str],
    has_chats: bool,
) -> dict[str, object]:
    event_id = str(event.get("event_id") or "unknown-event")
    items = [
        "Write the desired meeting outcome in one sentence",
        "Prepare three talking points and the evidence behind each",
        "List unresolved decisions and the owner needed for each",
    ]
    if str(event.get("self_rsvp_status") or "") == "needs_action":
        items.insert(0, "Confirm attendance")
    if event_docs:
        items.append("Review linked documents and extract changes since the last discussion")
    if has_chats:
        items.append("Review the selected chat context and capture open questions")
    document_status = _source_status(source_reads, "documents") if event_docs else "not_configured"
    note_status = _item_source_status(
        source_reads, "meeting_notes", event_id
    )
    source_brief = _source_brief(
        event,
        event_docs=event_docs,
        source_reads=source_reads,
        event_id=event_id,
    )
    if source_brief["meeting_note_excerpts"]:
        items.append("Reconcile prior decisions and unfinished actions from meeting notes")
    if source_brief["chat_excerpts"]:
        items.append("Address open questions and disagreements from the selected chat")
    return {
        "meeting_id": event_id,
        "title": str(event.get("summary") or "Untitled meeting"),
        "start": _nested_text(event, "start_time", "datetime"),
        "end": _nested_text(event, "end_time", "datetime"),
        "description": str(event.get("description") or "")[:4000],
        "rsvp_status": str(event.get("self_rsvp_status") or "unknown"),
        "source_coverage": {
            "calendar": "read",
            "meeting_notes": note_status,
            "documents": document_status,
            "chat": _source_status(source_reads, "chat"),
        },
        "preparation_items": items,
        "source_brief": source_brief,
    }


def _todo_projection(meeting: dict[str, object]) -> dict[str, object]:
    return {
        "todo_id": str(meeting["meeting_id"]),
        "source_id": f"lark-meeting:{meeting['meeting_id']}",
        "title": f"Prepare: {meeting['title']}",
        "status": "open",
        "priority": "P1",
        "task_class": "advancement_task",
        "action_kind": "prepare_meeting",
        "scope": "owner-private Lark meeting preparation",
        "due_at": meeting.get("start"),
        "evidence": f"meeting_prep_packet_v0; source_coverage={json.dumps(meeting.get('source_coverage'), sort_keys=True)}",
        "row_lifecycle": "active",
    }


def _document_sources_from_event(event: dict[str, Any]) -> list[str]:
    text = "\n".join(
        str(event.get(field) or "") for field in ("description", "description_rich")
    )
    return _DOC_LINK_RE.findall(text)


def _document_plan_commands(source: str, *, cli_bin: str) -> list[list[str]]:
    if "/wiki/" in source:
        return [[
            cli_bin,
            "--as",
            "user",
            "wiki",
            "+node-get",
            "--node-token",
            source,
            "--format",
            "json",
        ]]
    return [[cli_bin, "--as", "user", "docs", "+fetch", "--doc", source, "--format", "json"]]


def _read_document_source(
    source: str, *, cli_bin: str, runner: CommandRunner, cwd: Path
) -> dict[str, object]:
    commands = _document_plan_commands(source, cli_bin=cli_bin)
    if "/wiki/" not in source:
        return _safe_read(commands[0], runner=runner, cwd=cwd)
    resolved = _safe_read(commands[0], runner=runner, cwd=cwd)
    if resolved.get("status") != "read":
        return resolved
    node = _find_node_identity(resolved.get("payload"))
    if node is None:
        return {"status": "unavailable", "error": "wiki node response omitted obj_type or obj_token"}
    obj_type, obj_token = node
    if obj_type != "docx":
        return {
            "status": "unsupported",
            "reason": f"wiki object type {obj_type} is not a document",
            "wiki_node": resolved.get("payload"),
        }
    fetched = _safe_read(
        [cli_bin, "--as", "user", "docs", "+fetch", "--doc", obj_token, "--format", "json"],
        runner=runner,
        cwd=cwd,
    )
    fetched["wiki_node"] = resolved.get("payload")
    return fetched


def _find_node_identity(value: object) -> tuple[str, str] | None:
    if isinstance(value, dict):
        obj_type = value.get("obj_type")
        obj_token = value.get("obj_token")
        if isinstance(obj_type, str) and isinstance(obj_token, str):
            return obj_type, obj_token
        for nested in value.values():
            found = _find_node_identity(nested)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_node_identity(nested)
            if found is not None:
                return found
    return None


def _source_brief(
    event: dict[str, Any],
    *,
    event_docs: list[str],
    source_reads: dict[str, object],
    event_id: str,
) -> dict[str, object]:
    description = str(event.get("description") or "").strip()
    headings: list[str] = []
    excerpts: list[str] = []
    note_excerpts: list[str] = []
    chat_excerpts: list[str] = []
    documents = source_reads.get("documents")
    items = documents.get("items") if isinstance(documents, dict) else None
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict) or str(item.get("source") or "") not in event_docs:
                continue
            content = _find_document_content(item.get("payload"))
            if not content:
                continue
            headings.extend(re.findall(r"<h[1-3]>(.*?)</h[1-3]>", content, flags=re.DOTALL))
            plain = _plain_text(content)
            if plain:
                excerpts.append(plain[:1200])
    notes = source_reads.get("meeting_notes")
    note_items = notes.get("items") if isinstance(notes, dict) else None
    if isinstance(note_items, list):
        for item in note_items:
            if not isinstance(item, dict) or item.get("source") != event_id:
                continue
            documents = item.get("documents")
            if isinstance(documents, list):
                for document in documents:
                    if not isinstance(document, dict):
                        continue
                    content = _find_document_content(document.get("payload"))
                    plain = _plain_text(content)
                    if plain:
                        note_excerpts.append(plain[:1200])
            if not note_excerpts:
                note_excerpts.extend(_payload_excerpts(item.get("payload")))
    chats = source_reads.get("chat")
    chat_items = chats.get("items") if isinstance(chats, dict) else None
    if isinstance(chat_items, list):
        for item in chat_items:
            if isinstance(item, dict):
                chat_excerpts.extend(_payload_excerpts(item.get("payload")))
    return {
        "description": description[:1200],
        "document_headings": _unique_nonempty(_plain_text(item) for item in headings)[:8],
        "document_excerpts": excerpts[:3],
        "meeting_note_excerpts": note_excerpts[:3],
        "chat_excerpts": chat_excerpts[:5],
    }


def _item_source_status(
    source_reads: dict[str, object], name: str, source_id: str
) -> str:
    source = source_reads.get(name)
    items = source.get("items") if isinstance(source, dict) else None
    if not isinstance(items, list):
        return _source_status(source_reads, name)
    for item in items:
        if isinstance(item, dict) and item.get("source") == source_id:
            return str(item.get("status") or "unknown")
    return "not_available"


def _aggregate_item_status(items: list[dict[str, object]]) -> str:
    statuses = {str(item.get("status") or "unknown") for item in items}
    if not statuses:
        return "not_configured"
    if statuses == {"read"}:
        return "read"
    if "read" in statuses:
        return "partial"
    return sorted(statuses)[0]


def _note_document_tokens(value: object) -> list[str]:
    tokens: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"note_doc_token", "meeting_notes"}:
                if isinstance(nested, str) and nested.strip():
                    tokens.append(nested.strip())
                elif isinstance(nested, list):
                    tokens.extend(str(item).strip() for item in nested if str(item).strip())
            tokens.extend(_note_document_tokens(nested))
    elif isinstance(value, list):
        for nested in value:
            tokens.extend(_note_document_tokens(nested))
    return _unique_nonempty(tokens)


def _payload_excerpts(value: object) -> list[str]:
    excerpts: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in {"content", "text", "summary", "title"} and isinstance(nested, str):
                plain = _plain_text(nested)
                if plain:
                    excerpts.append(plain[:1200])
            else:
                excerpts.extend(_payload_excerpts(nested))
    elif isinstance(value, list):
        for nested in value:
            excerpts.extend(_payload_excerpts(nested))
    return _unique_nonempty(excerpts)[:10]


def _find_document_content(value: object) -> str:
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, str):
            return content
        for nested in value.values():
            found = _find_document_content(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_document_content(nested)
            if found:
                return found
    return ""


def _plain_text(value: str) -> str:
    without_tags = re.sub(r"<[^>]+>", " ", value)
    return " ".join(without_tags.replace("&amp;", "&").split())


def _chat_start_date(target_date: str) -> str:
    try:
        return (date.fromisoformat(target_date) - timedelta(days=7)).isoformat()
    except ValueError as exc:
        raise ValueError("target date must use YYYY-MM-DD") from exc


def _source_status(source_reads: dict[str, object], name: str) -> str:
    source = source_reads.get(name)
    if not isinstance(source, dict):
        return "unknown"
    return str(source.get("status") or "unknown")


def _event_sort_key(event: dict[str, Any]) -> str:
    return _nested_text(event, "start_time", "datetime")


def _nested_text(payload: dict[str, Any], key: str, nested_key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, dict):
        return ""
    return str(value.get(nested_key) or "")


def _unique_nonempty(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _read_json_object(path: Path) -> dict[str, object]:
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("fixture JSON exceeds the 4 MiB limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("fixture must be a JSON object")
    return payload


def _compact_error(result: dict[str, object]) -> str:
    detail = str(result.get("stderr") or result.get("stdout") or "command failed")
    return " ".join(detail.split())[:500]


def _payload_error(payload: dict[str, object]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or "lark-cli failed")[:500]
    return str(error or "lark-cli failed")[:500]
