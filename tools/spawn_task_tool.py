#!/usr/bin/env python3
"""Offer a side task the user can launch as a separate session (``desktop_ui`` only).

The tool starts nothing. The desktop renders the call as a chip where the user
picks the model and effort, then decides how to run it (new tab, new worktree)
or dismisses it. The chip reads the call's own args, so it survives a reload and
needs no event or round-trip; the result is only a receipt for the model.
"""

import json

from tools.registry import registry, tool_error


def spawn_task_tool(title: str, prompt: str, tldr: str = "") -> str:
    """Validate the offer and acknowledge it. The user owns everything after."""
    title = (title or "").strip()
    prompt = (prompt or "").strip()
    if not title:
        return tool_error("spawn_task needs a short title for the chip.")
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


registry.register(
    name="spawn_task",
    toolset="desktop_ui",
    schema=SPAWN_TASK_SCHEMA,
    handler=lambda args, **kw: spawn_task_tool(
        title=args.get("title", ""), prompt=args.get("prompt", ""), tldr=args.get("tldr", "")),
    emoji="🧩",
)
