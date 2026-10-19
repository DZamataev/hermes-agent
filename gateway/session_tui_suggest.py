"""The composer ghost (``complete.suggest``) on the shared owner.

Desktop's local sessions run their turns here, never through the sidecar's prompt loop that
computes the suggestion at turn end (``tui_gateway/suggest_cache.py``), so the owner does the same
at its own completion frame and serves the read. Only local sessions get one: a messaging chat has
no composer, and a prediction bills a request per turn.
"""
import asyncio
import logging

from gateway.config import Platform
from gateway.session_busy_controls import authorize
from hermes_state_runtime import RuntimeStoreError

logger = logging.getLogger(__name__)

_WAIT_SECONDS = 75.0  # the sidecar's own wait: longer than the prediction's hung-request guard


def schedule_after_turn(authority, live, ref):
    """Start the suggestion for the turn that just completed. Called before its ``message.complete``
    is published, so a client asking on that event finds the record."""
    source = getattr(live, 'source', None)
    if source is None or source.platform != Platform.LOCAL:
        return
    try:
        _start(authority, live, ref)
    except Exception:  # health: allow BLE001 -- a suggestion must never withhold the completion frame
        live.composer_suggestion = None  # a reader must not wait on a record nobody fills
        logger.debug('composer suggestion could not be scheduled', exc_info=True)


def _start(authority, live, ref):
    from tui_gateway.suggest_cache import _TurnSuggestion
    record = live.composer_suggestion = _TurnSuggestion(0)
    agent = authority.agent(ref)
    history = list(getattr(agent, '_session_messages', None) or [])

    def run():
        try:
            from gateway.session_policy import policy_for_source
            from gateway.session_tui_side import _side_scope
            from tui_gateway.suggest_cache import compute
            policy = policy_for_source(authority.runner, live.source)
            if policy is None:
                return
            with _side_scope(authority, policy, f'suggest_{ref.session_id}'):
                display = policy.config(authority).get('display') or {}
                if display.get('composer_suggestions', True):
                    # The FIFO claim stamps ``executing``: a new turn makes the guess stale.
                    record.candidates = compute(history, agent, lambda: ref.session_id in authority.executing, display)
        except Exception:  # health: allow BLE001 -- decorative: a failed suggestion is no suggestion
            logger.debug('composer suggestion failed', exc_info=True)
        finally:
            record.done.set()

    from agent.memory_provider import spawn_context_thread
    spawn_context_thread(run, name='composer-suggestion').start()


async def suggest(connection, ref, params):
    if set(params) - {'session_id', 'profile'} or not isinstance(ref.session_id, str) or not ref.session_id:
        raise RuntimeStoreError('invalid_params')
    authority = connection.authority
    live = authority.sessions.get(ref.session_id)
    if live is None:
        raise RuntimeStoreError('not_found')
    authorize(connection, ref, params, 'session:read')
    record = live.composer_suggestion
    if record is None:
        return {'candidates': []}  # no turn completed on this owner yet: never a model request on demand
    done = await asyncio.to_thread(record.done.wait, _WAIT_SECONDS)
    return {'candidates': list(record.candidates) if done else []}
