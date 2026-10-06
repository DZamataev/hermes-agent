#!/usr/bin/env python3
"""Offer a side task the user can launch as a separate session (``desktop_ui`` only).

The tool starts nothing. The desktop renders the call as a chip where the user
picks the model and effort, then decides how to run it (new tab, new worktree)
or dismisses it. The chip reads the call's own args, so it survives a reload and
needs no event or round-trip; the result is only a receipt for the model.
"""

import json
import re

from tools.registry import no_cache_check_fn, registry, tool_error


def spawn_task_tool(title: str, prompt: str, tldr: str = "") -> str:
    """Validate the offer and acknowledge it. The user owns everything after."""
    from hermes_state import SessionDB

    # The title becomes the new session's title: clean it the way the store
    # will, and keep it inside the store's limit (an over-long title is dropped
    # silently at the first turn, leaving the session to auto-title instead).
    # Cut before sanitizing (sanitize raises past the limit), then again after
    # (whitespace collapse never lengthens, so this only trims the tail).
    limit = SessionDB.MAX_TITLE_LENGTH
    title = (SessionDB.sanitize_title((title or "")[:limit]) or "")[:limit].strip()
    # Name lookups (``-c "<t>"``, ``/resume <t>``) prefer ANY "<t> #…" over
    # "<t>" itself (``LIKE '<t> #%'``), so a model-written "Refactor auth #2"
    # or "… #followup" would hijack the user's "Refactor auth". Keep the
    # suffix text, drop the " #" lineage shape.
    title = re.sub(r" #(\S*)", lambda m: f" ({m.group(1)})" if m.group(1) else "", title).strip()
    # The rewrite adds a character per " #x": cut again, so the receipt never
    # exceeds what the store accepts (an over-long title is refused there).
    title = title[:limit].rstrip()
    prompt = (prompt or "").strip()
    if not title:
        return tool_error("spawn_task needs a short title for the chip.")
    # A registry name: a session titled like the canonical Bot Chat would be
    # resolved as that profile's bot chat.
    if title.casefold() == SessionDB.CANONICAL_BOT_CHAT_TITLE.casefold():
        return tool_error(f"'{SessionDB.CANONICAL_BOT_CHAT_TITLE}' is reserved; pick a title naming the task.")
    if not prompt:
        return tool_error("spawn_task needs a self-contained prompt for the new session.")
    return json.dumps({
        "success": True, "status": "offered", "title": title,
        "note": "Shown to the user as a chip; nothing runs unless they launch it. Carry on with your task.",
    }, ensure_ascii=False)


SPAWN_TASK_SCHEMA = {
    "name": "spawn_task",
    "description": (
        "Offer the user a side task as a clickable chip that launches a separate Hermes "
        "session — for work you noticed that is real but outside the current task "
        "(a bug elsewhere, a follow-up refactor, missing tests). Nothing runs until the "
        "user picks a model and launches it, so keep going with your own task and do not "
        "do the side work yourself. The prompt must stand alone: the new session sees "
        "none of this conversation, so name files, symptoms and the expected outcome. "
        "One chip per distinct task; skip trivia."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Chip label, under ~60 characters.",
            },
            "prompt": {
                "type": "string",
                "description": "Full self-contained instructions for the new session.",
            },
            "tldr": {
                "type": "string",
                "description": "One sentence on why it matters, shown under the title.",
            },
        },
        "required": ["title", "prompt"],
    },
}


@no_cache_check_fn
def check_spawn_task_available() -> bool:
    """Withdrawn for delegate_task children. Their offer renders no chip that
    anyone could launch, and the success reply would tell the model the user saw
    one. Listing the name in DELEGATE_BLOCKED_TOOLS is not enough, because it
    only strips toolsets made up entirely of blocked tools, and ``desktop_ui``
    is a mixed toolset. Uncached: the answer depends on WHO is building
    (a task-local ContextVar), and the process-wide check_fn cache is not keyed
    by it — a cached parent answer would leak to the child and vice versa."""
    from agent.delegation_context import is_delegated_child_context

    return not is_delegated_child_context()


registry.register(
    name="spawn_task",
    toolset="desktop_ui",
    schema=SPAWN_TASK_SCHEMA,
    check_fn=check_spawn_task_available,
    handler=lambda args, **kw: spawn_task_tool(
        title=args.get("title", ""), prompt=args.get("prompt", ""), tldr=args.get("tldr", "")),
    emoji="🧩",
)
