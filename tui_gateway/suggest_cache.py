"""Composer suggestions, computed once at the end of the turn they answer.

The model prediction rides the session's warm prompt cache, which only holds right after the turn
that warmed it. Asking when a composer happens to show the session (minutes later, or never) would
replay a cold conversation. So the gateway computes the suggestion as soon as a turn completes,
keeps it on the session for that history version, and every client reads that one result through
``complete.suggest`` (Desktop or TUI, however many are attached): no request per client, and no
model request on opening a session.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_KEY = "_composer_suggestion"
# Longer than the prediction's own 60 s hung-request guard: a reader waits for the turn's result
# rather than answering without it.
WAIT_SECONDS = 75.0


@dataclass
class _TurnSuggestion:
    version: int
    done: threading.Event = field(default_factory=threading.Event)
    candidates: list[dict] = field(default_factory=list)


def _display() -> dict:
    from tui_gateway.server import _load_cfg

    return _load_cfg().get("display") or {}


def _snapshot(session: dict) -> tuple[list, int, Any]:
    """History, its version and the in-flight turn, read together: turn admission holds
    ``history_lock`` too, so a turn starting right after is always seen as new."""
    def read() -> tuple[list, int, Any]:
        return list(session.get("history", [])), int(session.get("history_version", 0)), session.get("inflight_turn")

    lock = session.get("history_lock")
    if lock is None:
        return read()
    with lock:
        return read()


def _extracted(history: list) -> list[dict]:
    """The free deterministic replies for the last assistant message."""
    from tui_gateway.server import _coerce_message_text
    from tui_gateway.suggest import extract_suggestions

    assistant_text = next(
        (_coerce_message_text(m.get("content")) or "" for m in reversed(history) if m.get("role") == "assistant"), "")
    return [{"text": c.text, "kind": c.kind} for c in extract_suggestions(assistant_text)]


def _superseded(session: dict, version: int, turn_at_start: Any):
    had_turn = isinstance(turn_at_start, dict)
    started_at = turn_at_start.get("started_at") if had_turn else None

    def superseded() -> bool:
        """A new turn makes any guess stale. Admission marks it at once (a fresh in-flight turn);
        ``history_version`` only moves when that turn's result is committed. The turn that was
        still being cleared when the prediction started is not a new one."""
        turn = session.get("inflight_turn")
        started = isinstance(turn, dict) and (not had_turn or turn.get("started_at") != started_at)
        return started or int(session.get("history_version", 0)) != version

    return superseded


def compute(history: list, agent: Any, superseded, display: dict) -> list[dict]:
    """The model prediction (``display.composer_predictions``, default on), with the extractor as
    the fallback when the model abstains; the extractor alone when predictions are off.
    ``superseded`` is true once a new turn makes the guess stale."""
    if bool(display.get("composer_predictions", True)):
        from tui_gateway.server import _coerce_message_text, _main_runtime_from_agent
        from tui_gateway.suggest_predict import predict_next_message

        prediction = predict_next_message(
            history, _coerce_message_text, main_runtime=_main_runtime_from_agent(agent), agent=agent,
            stale=superseded)
        if superseded():
            return []
        if prediction:
            return [{"text": prediction, "kind": "prediction"}]
    return _extracted(history)


def schedule_after_turn(session: dict) -> None:
    """Start computing the suggestion for the turn that just completed (its history is committed).
    Called before ``message.complete`` is emitted, so a client asking on that event finds it."""
    try:
        display = _display()
        # A hosted-room turn is driven by the room, not by a person about to type a reply.
        if not bool(display.get("composer_suggestions", True)) or session.get("_hosted_room_task"):
            return
        history, version, turn = _snapshot(session)
        record = _TurnSuggestion(version)
        session[_KEY] = record

        def run() -> None:
            try:
                record.candidates = compute(history, session.get("agent"), _superseded(session, version, turn), display)
            except Exception:  # health: allow BLE001 -- decorative: a failed suggestion is no suggestion
                logger.debug("composer suggestion failed", exc_info=True)
            finally:
                record.done.set()

        from agent.memory_provider import spawn_context_thread

        # Context-bound: the prediction resolves config and credentials under the turn's profile.
        spawn_context_thread(run, name="composer-suggestion").start()
    except Exception:  # health: allow BLE001 -- a suggestion must never disturb the turn that ends
        logger.debug("composer suggestion could not be scheduled", exc_info=True)


def suggestions_for(session: dict, *, wait: float = WAIT_SECONDS) -> dict:
    """The suggestion for the session's current history version, waiting for one still being
    computed. Without one (no turn has completed since the gateway started, or the history moved
    without a turn) only the free extractor answers: a model request is never made on demand."""
    history, version, _turn = _snapshot(session)
    record = session.get(_KEY)
    if isinstance(record, _TurnSuggestion) and record.version == version:
        done = record.done.wait(wait)
        return {"candidates": list(record.candidates) if done else [], "history_version": version}
    return {"candidates": _extracted(history), "history_version": version}
