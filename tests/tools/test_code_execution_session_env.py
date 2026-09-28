"""execute_code children see the calling session's identity, like a terminal child does.

Scripts that subscribe the orchestrating session to Kanban cards (``kanban-chain.py`` via
``kanban-card.sh``) read ``HERMES_SESSION_KEY``. The execute_code scrub dropped it, so a chain
launched from execute_code created its cards with no subscription for the session and every
completion/block notice for that chain went nowhere in the desktop.
"""

import sys

import pytest

from gateway.session_context import clear_session_vars, set_session_vars
from tools.code_execution_env import _build_child_env


def _child_env():
    return _build_child_env(rpc_endpoint="sock", rpc_token="tok", tmpdir="/tmp/hermes-test",
                            child_python=sys.executable)


@pytest.fixture
def bound_session():
    tokens = set_session_vars(platform="", source="desktop", session_key="20260928_010203_abcdef",
                              session_id="20260928_010203_abcdef")
    yield
    clear_session_vars(tokens)


def test_child_sees_the_bound_session_key(bound_session):
    assert _child_env().get("HERMES_SESSION_KEY") == "20260928_010203_abcdef"


def test_bound_context_wins_over_a_stale_process_mirror(monkeypatch, bound_session):
    # One serve process hosts many sessions; os.environ holds whichever turn wrote last.
    monkeypatch.setenv("HERMES_SESSION_KEY", "another_sessions_key")
    assert _child_env().get("HERMES_SESSION_KEY") == "20260928_010203_abcdef"


def test_secrets_stay_scrubbed(monkeypatch, bound_session):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
    assert "OPENAI_API_KEY" not in _child_env()


def test_an_unbound_thread_in_an_engaged_process_gets_no_key(monkeypatch, bound_session):
    """Once any session is bound, the process mirror belongs to whichever turn wrote last; a context that bound
    none must pass no key (as the terminal does), or a chain from it would subscribe another session."""
    import threading

    monkeypatch.setenv("HERMES_SESSION_KEY", "another_sessions_key")
    seen = {}
    t = threading.Thread(target=lambda: seen.update(key=_child_env().get("HERMES_SESSION_KEY")))
    t.start()
    t.join()
    assert seen["key"] is None


def test_a_gateway_turn_passes_the_chat_with_the_key():
    """kanban-card.sh tells a gateway session from a desktop one by HERMES_SESSION_PLATFORM: a child that got only the
    key would subscribe a desktop-poller row nobody serves."""
    tokens = set_session_vars(platform="telegram", chat_id="123", chat_type="group", thread_id="7",
                              session_key="agent:main:telegram:group:123:7")
    try:
        env = _child_env()
    finally:
        clear_session_vars(tokens)
    assert env.get("HERMES_SESSION_KEY") == "agent:main:telegram:group:123:7"
    assert env.get("HERMES_SESSION_PLATFORM") == "telegram"
    assert env.get("HERMES_SESSION_CHAT_ID") == "123"
    assert env.get("HERMES_SESSION_THREAD_ID") == "7"


def test_other_session_vars_are_not_borrowed_from_another_turn(monkeypatch, bound_session):
    import threading

    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    monkeypatch.setenv("HERMES_SESSION_CHAT_ID", "someone-else")
    seen = {}
    t = threading.Thread(target=lambda: seen.update(_child_env()))
    t.start()
    t.join()
    assert "HERMES_SESSION_PLATFORM" not in seen and "HERMES_SESSION_CHAT_ID" not in seen


def test_a_plain_cli_process_passes_its_own_env(monkeypatch):
    """A CLI that never bound a session (not a serve/gateway process) keeps the os.environ mirror, as the terminal
    tool does: that env is the session's own."""
    import contextvars

    import gateway.session_context as sc

    monkeypatch.setattr(sc, "session_context_engaged", lambda: False)
    monkeypatch.setenv("HERMES_SESSION_KEY", "cli_session")
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "telegram")
    env = contextvars.Context().run(_child_env)  # nothing bound, as in a plain CLI
    assert env.get("HERMES_SESSION_KEY") == "cli_session"
    assert env.get("HERMES_SESSION_PLATFORM") == "telegram"
