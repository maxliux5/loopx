from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from ..extensions.lark import LARK_EXTENSION_ID, LARK_MEETING_PREP_PERMISSION
from ..extensions.lark.meeting_prep import (
    build_lark_meeting_prep_plan,
    compact_lark_meeting_prep_packet,
    default_target_date,
    prepare_lark_meetings,
    render_lark_meeting_prep_markdown,
)
from ..extensions.runtime import default_extension_state_file, resolve_extension_activation
from ..history import load_registry
from ..paths import resolve_runtime_root


PrintPayload = Callable[[dict[str, object], str, Callable[[dict[str, object]], str]], None]
OutputFormat = Callable[[argparse.Namespace], str]


def register_lark_meeting_prep_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "lark-meeting-prep",
        help="Prepare meetings from bounded Lark calendar, notes, document, and chat reads.",
    )
    sub = parser.add_subparsers(dest="lark_meeting_prep_command", required=True)

    plan = sub.add_parser("plan", help="Preview the Lark read boundary and exact commands.")
    add_subcommand_format(plan)
    _add_source_args(plan)

    prepare = sub.add_parser(
        "prepare",
        help="Build an owner-private meeting packet. Requires --execute or --agenda-fixture.",
    )
    add_subcommand_format(prepare)
    _add_source_args(prepare)
    prepare.add_argument("--agenda-fixture", help="Sanitized calendar +agenda JSON fixture.")
    prepare.add_argument("--event-id", action="append", default=[], help="Only prepare this event id. Repeatable.")
    prepare.add_argument("--execute", action="store_true", help="Run the read-only lark-cli commands.")
    prepare.add_argument(
        "--output-private",
        help="Also save the packet below a .loopx directory; intended for ignored owner-private state.",
    )


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--date", default=default_target_date(), help="Target date (YYYY-MM-DD); defaults to tomorrow.")
    parser.add_argument("--chat-id", action="append", default=[], help="Explicit related chat id. Repeatable.")
    parser.add_argument("--doc", action="append", default=[], help="Explicit related document URL or token. Repeatable.")
    parser.add_argument("--cli-bin", default="lark-cli")


def handle_lark_meeting_prep_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
    output_format: OutputFormat,
) -> int | None:
    if args.command != "lark-meeting-prep":
        return None
    fmt = output_format(args)
    try:
        activation_runtime_root = (
            Path(runtime_root_arg).expanduser()
            if runtime_root_arg is not None
            else resolve_runtime_root(load_registry(registry_path), None)
        )
        activation = resolve_extension_activation(
            LARK_EXTENSION_ID,
            state_file=default_extension_state_file(activation_runtime_root),
            required_permissions=(LARK_MEETING_PREP_PERMISSION,),
        )
        if args.lark_meeting_prep_command == "plan":
            payload = build_lark_meeting_prep_plan(
                target_date=args.date,
                chat_ids=args.chat_id,
                docs=args.doc,
                cli_bin=args.cli_bin,
            )
        elif args.lark_meeting_prep_command == "prepare":
            output_path = (
                _private_output_path(args.output_private)
                if args.output_private
                else None
            )
            payload = prepare_lark_meetings(
                target_date=args.date,
                agenda_fixture=Path(args.agenda_fixture).expanduser() if args.agenda_fixture else None,
                chat_ids=args.chat_id,
                docs=args.doc,
                event_ids=args.event_id,
                execute=bool(args.execute),
                cli_bin=args.cli_bin,
                private_workdir=(
                    output_path.parent / "connector-runtime"
                    if output_path is not None
                    else None
                ),
            )
            full_payload = payload
            if args.output_private:
                assert output_path is not None
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(full_payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                payload = compact_lark_meeting_prep_packet(full_payload)
                payload["private_output"] = str(output_path)
            else:
                payload = compact_lark_meeting_prep_packet(full_payload)
        else:
            raise ValueError(f"unknown lark-meeting-prep command: {args.lark_meeting_prep_command}")
        payload["extension_activation"] = activation
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "meeting_prep_error_v0",
            "error": str(exc),
        }
    print_payload(payload, fmt, render_lark_meeting_prep_markdown)
    return 0 if payload.get("ok") else 1


def _private_output_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser().resolve()
    if not any(parent.name == ".loopx" for parent in path.parents):
        raise ValueError("--output-private must be stored below a .loopx directory")
    return path
