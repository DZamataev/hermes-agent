"""The session's forced subagent route — the Desktop composer's "Subagents" pick.

One shape everywhere: ``{"provider": str, "model": str, "reasoning_effort": str}`` with a non-empty ``model``
(``provider``/``reasoning_effort`` may be "" = inherit). It lives on the live session record
(``delegation_override``), on the live agent (``_delegation_override``, read by ``delegate_task``), and in the
row's ``model_config`` so a resumed chat keeps it. It is never written to config.yaml: the operator's global
tier routes live in the ``delegation`` block and stay untouched by a per-chat pick.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

_FIELDS = ("provider", "model", "reasoning_effort")
CLEAR_WORDS = frozenset({"", "auto", "clear", "none", "off"})
MODEL_CONFIG_KEY = "delegation_override"


def normalize_delegation_pick(value: Any) -> Optional[dict]:
    """A validated pick, or None when *value* is not one (no model / unknown effort / not a mapping)."""
    if not isinstance(value, dict):
        return None
    if any(value.get(k) is not None and not isinstance(value.get(k), str) for k in _FIELDS):
        return None  # {"model": 5} is a malformed frame/RPC value, not a model named "5"
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
    return normalize_delegation_pick(model_config.get(MODEL_CONFIG_KEY))


def stamp_model_config(model_config: dict, session: dict) -> dict:
    """Write the session's pick into a row ``model_config`` (or drop a stale one); returns the same dict."""
    pick = session.get("delegation_override")
    if isinstance(pick, dict):
        model_config[MODEL_CONFIG_KEY] = dict(pick)
    else:
        model_config.pop(MODEL_CONFIG_KEY, None)
    return model_config


def apply_delegation_override(session: dict, agent: Any) -> None:
    """Mirror the session's pick onto *agent* (None clears) — every place a session gets a new agent calls this.

    Also stamps the agent's ``_session_init_model_config``: that dict seeds the row the agent lazily creates and
    the continuation row a compression rotation publishes, so a pick made before either would otherwise vanish
    on the next resume."""
    if agent is None:
        return
    pick = session.get("delegation_override")
    try:
        agent._delegation_override = dict(pick) if isinstance(pick, dict) else None
    except AttributeError:  # a slotted stand-in agent: the pick is advisory, never worth failing a build over
        logger.debug("agent %r cannot carry a subagent pick", type(agent).__name__)
        return
    init_cfg = getattr(agent, "_session_init_model_config", None)
    if isinstance(init_cfg, dict):
        stamp_model_config(init_cfg, session)


def persist_delegation_pick(session: dict) -> None:
    """Write the session's pick into its EXISTING row without a live agent (a lazy / not-yet-built session).
    No row yet = nothing to do: the first prompt's row write stamps it (``_workdir_row_model_config``)."""
    key = str(session.get("session_key") or "")
    if not key:
        return
    from tui_gateway import server  # helper bodies are rebound onto server.py globals at install time
    with server._session_db(session) as db:
        if db is None:
            return
        try:
            # One atomic merge (None removes the key): a read-modify-write here raced other model_config
            # writers on the same row (yolo, model switch, runtime lock) and silently dropped their keys.
            pick = session.get("delegation_override")
            db.patch_session_model_config(key, {MODEL_CONFIG_KEY: dict(pick) if isinstance(pick, dict) else None})
        except Exception:
            logger.warning("failed to persist subagent pick for %s", key, exc_info=True)
