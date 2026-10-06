"""The session's forced subagent route — the Desktop composer's "Subagents" pick.

One shape everywhere: ``{"provider": str, "model": str, "reasoning_effort": str}`` with a non-empty ``model``
(``provider``/``reasoning_effort`` may be "" = inherit). It lives on the live session record
(``delegation_override``), on the live agent (``_delegation_override``, read by ``delegate_task``), and in the
row's ``model_config`` so a resumed chat keeps it. It is never written to config.yaml: the operator's global
tier routes live in the ``delegation`` block and stay untouched by a per-chat pick.
"""

from __future__ import annotations

from typing import Any, Optional

_FIELDS = ("provider", "model", "reasoning_effort")
CLEAR_WORDS = frozenset({"", "auto", "clear", "none", "off"})


def normalize_delegation_pick(value: Any) -> Optional[dict]:
    """A validated pick, or None when *value* is not one (no model / unknown effort / not a mapping)."""
    if not isinstance(value, dict):
        return None
    pick = {k: str(value.get(k) or "").strip() for k in _FIELDS}
    if not pick["model"]:
        return None
    if pick["reasoning_effort"]:
        from hermes_constants import parse_reasoning_effort
        if parse_reasoning_effort(pick["reasoning_effort"]) is None:
            return None
    return pick


def delegation_override_from_model_config(model_config: Any) -> Optional[dict]:
    """The pick a stored row carries; malformed or absent = None (Auto)."""
    if not isinstance(model_config, dict):
        return None
    return normalize_delegation_pick(model_config.get("delegation_override"))


def apply_delegation_override(session: dict, agent: Any) -> None:
    """Mirror the session's pick onto *agent* (None clears)."""
    if agent is None:
        return
    pick = session.get("delegation_override")
    agent._delegation_override = dict(pick) if isinstance(pick, dict) else None
