#!/usr/bin/env python3
"""Smoke-test the bounded Lark meeting-preparation adapter."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.extensions.lark.meeting_prep import (  # noqa: E402
    build_lark_meeting_prep_plan,
    prepare_lark_meetings,
)
from loopx.extensions.lark.presentation.projection_rows import (  # noqa: E402
    projection_rows_from_payload,
)
from loopx.cli_commands.lark_meeting_prep import _private_output_path  # noqa: E402
from examples.lark_extension_test_support import install_bundled_lark_extension  # noqa: E402


AGENDA_FIXTURE = {
    "ok": True,
    "identity": "user",
    "data": [
        {
            "event_id": "event_public_fixture_0",
            "summary": "Public planning fixture",
            "description": "Review https://example.invalid/docx/doc_public_fixture",
            "start_time": {"datetime": "2026-07-22T10:00:00+08:00"},
            "end_time": {"datetime": "2026-07-22T10:30:00+08:00"},
            "self_rsvp_status": "needs_action",
        }
    ],
    "meta": {"count": 1},
}


class FixtureRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path | None]] = []

    def __call__(
        self, args: list[str], cwd: Path | None, timeout: float | None
    ) -> dict[str, object]:
        self.calls.append((args, cwd))
        if "+notes" in args:
            payload = {
                "ok": True,
                "data": {"notes": [{"note_doc_token": "doc_note_public_fixture"}]},
            }
        elif "+fetch" in args:
            doc = args[args.index("--doc") + 1]
            content = (
                "<title>Prior notes</title><p>Decision: keep the bounded rollout.</p>"
                if doc == "doc_note_public_fixture"
                else "<title>Planning source</title><h1>Launch gate</h1><p>Review evidence.</p>"
            )
            payload = {"ok": True, "data": {"document": {"content": content}}}
        elif "+chat-messages-list" in args:
            assert "--order" in args and args[args.index("--order") + 1] == "asc", args
            payload = {
                "ok": True,
                "data": {"messages": [{"content": {"text": "Open question: who owns the rollout gate?"}}]},
            }
        else:
            raise AssertionError(args)
        return {"returncode": 0, "stdout": json.dumps(payload), "stderr": ""}


def main() -> None:
    try:
        _private_output_path(".loopx/../public-packet.json")
    except ValueError as error:
        assert "below a .loopx directory" in str(error), error
    else:
        raise AssertionError("private output accepted a path that escapes .loopx")
    plan = build_lark_meeting_prep_plan(
        target_date="2026-07-22",
        chat_ids=("oc_public_fixture",),
        docs=("doc_public_fixture",),
    )
    assert plan["ok"] is True, plan
    assert plan["schema_version"] == "meeting_prep_plan_v0", plan
    assert plan["source_boundary"]["raw_storage"] == "project_local_ignored", plan
    assert plan["commands"][0] == [
        "lark-cli",
        "--as",
        "user",
        "calendar",
        "+agenda",
        "--start",
        "2026-07-22",
        "--end",
        "2026-07-22",
        "--format",
        "json",
    ], plan

    with tempfile.TemporaryDirectory(prefix="loopx-lark-meeting-prep-") as tmp:
        fixture = Path(tmp) / "agenda.json"
        fixture.write_text(json.dumps(AGENDA_FIXTURE), encoding="utf-8")
        packet = prepare_lark_meetings(
            target_date="2026-07-22",
            agenda_fixture=fixture,
            execute=False,
        )

    assert packet["ok"] is True, packet
    assert packet["schema_version"] == "meeting_prep_packet_v0", packet
    assert packet["meeting_count"] == 1, packet
    assert packet["meetings"][0]["source_coverage"] == {
        "calendar": "read",
        "meeting_notes": "planned",
        "documents": "planned",
        "chat": "not_configured",
    }, packet
    assert "Confirm attendance" in packet["meetings"][0]["preparation_items"], packet
    assert packet["todo_projection"][0]["source_id"] == "lark-meeting:event_public_fixture_0", packet
    assert "raw_sources" not in packet["todo_projection"][0], packet
    _, projection_rows, projection_warnings = projection_rows_from_payload(
        packet,
        goal_id=None,
        agent_id=None,
        source_id=str(packet["source_id"]),
        include_done=False,
        limit=50,
    )
    assert projection_warnings == [], projection_warnings
    assert len(projection_rows) == 1, projection_rows
    assert projection_rows[0]["action_kind"] == "prepare_meeting", projection_rows

    with tempfile.TemporaryDirectory(prefix="loopx-lark-meeting-prep-execute-") as tmp:
        private_workdir = Path(tmp) / ".loopx" / "connector-runtime"
        agenda_fixture = Path(tmp) / "agenda.json"
        agenda_fixture.write_text(json.dumps(AGENDA_FIXTURE), encoding="utf-8")
        runner = FixtureRunner()
        executed = prepare_lark_meetings(
            target_date="2026-07-22",
            agenda_fixture=agenda_fixture,
            chat_ids=("oc_public_fixture",),
            execute=True,
            private_workdir=private_workdir,
            runner=runner,
        )
    executed_meeting = executed["meetings"][0]
    assert executed_meeting["source_coverage"]["meeting_notes"] == "read", executed
    assert executed_meeting["source_coverage"]["chat"] == "read", executed
    assert "bounded rollout" in executed_meeting["source_brief"]["meeting_note_excerpts"][0], executed
    assert "rollout gate" in executed_meeting["source_brief"]["chat_excerpts"][0], executed
    assert any(
        item.startswith("Reconcile prior decisions")
        for item in executed_meeting["preparation_items"]
    ), executed
    assert all(cwd == private_workdir.resolve() for _, cwd in runner.calls), runner.calls

    with tempfile.TemporaryDirectory(prefix="loopx-lark-meeting-prep-cli-") as tmp:
        root = Path(tmp)
        registry = root / "registry.json"
        runtime_root = root / "runtime"
        fixture = root / "agenda.json"
        private_output = root / ".loopx" / "meeting-prep" / "packet.json"
        registry.write_text('{"goals": []}\n', encoding="utf-8")
        fixture.write_text(json.dumps(AGENDA_FIXTURE), encoding="utf-8")
        install_bundled_lark_extension(
            repo_root=REPO_ROOT,
            registry=registry,
            runtime_root=runtime_root,
        )
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "loopx.cli",
                "--format",
                "json",
                "--registry",
                str(registry),
                "--runtime-root",
                str(runtime_root),
                "lark-meeting-prep",
                "prepare",
                "--date",
                "2026-07-22",
                "--agenda-fixture",
                str(fixture),
                "--output-private",
                str(private_output),
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        cli_packet = json.loads(completed.stdout)
        stored_packet = json.loads(private_output.read_text(encoding="utf-8"))
    assert cli_packet["ok"] is True, cli_packet
    assert cli_packet["extension_activation"]["enabled"] is True, cli_packet
    assert stored_packet["schema_version"] == "meeting_prep_packet_v0", stored_packet
    print("lark-meeting-prep-smoke: ok")


if __name__ == "__main__":
    main()
