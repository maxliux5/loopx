# Lark meeting preparation

The bundled Lark extension can build an owner-private preparation packet from
user-authorized Lark reads. It uses `lark-cli` for authentication and keeps
credentials outside LoopX.

## Workflow

Preview the read boundary first:

```bash
loopx lark-meeting-prep plan --date 2026-07-22
```

Then execute the read-only workflow and store full connector responses below
the ignored project-local `.loopx/` directory:

```bash
loopx lark-meeting-prep prepare \
  --date 2026-07-22 \
  --execute \
  --output-private .loopx/meeting-prep/2026-07-22/packet.json
```

The default workflow reads calendar events, attempts to resolve meeting notes
by calendar event ID, and resolves document links found in event descriptions.
Wiki links are resolved to their real object type before document content is
read. Related chats are never guessed from a title; pass an explicit
`--chat-id` when a chat belongs to the preparation scope. Explicit `--chat-id`
and `--doc` sources require exactly one selected meeting; use `--event-id` to
bind them without leaking one meeting's context into another. Explicit chat
reads use a bounded seven-day lookback window.

For offline validation, `prepare` accepts a sanitized `--agenda-fixture`
instead of `--execute`.

## Output boundary

`meeting_prep_packet_v0` contains:

- a time-ordered meeting list;
- per-meeting source coverage and bounded source briefs;
- deterministic preparation prompts;
- an `agent_todos` projection accepted by `lark-kanban sync-projection`.

Raw connector bodies are written only when `--output-private` points below a
`.loopx` directory. The generated todo projection carries stable source IDs,
status, due time, lifecycle, and compact evidence; it does not carry raw chat
messages or document bodies.

Preview a board sync before enabling the write:

```bash
loopx lark-kanban sync-projection \
  --projection-file .loopx/meeting-prep/2026-07-22/packet.json \
  --sink-visibility owner-only
```

Run the same command with `--execute` after reviewing the projected rows. An
existing board created by an older LoopX release may need one schema upgrade:

```bash
loopx lark-kanban setup --execute
```
