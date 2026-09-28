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
