"""``replace_task`` / ``hermes kanban replace OLD --with NEW``: swap a card in a chain in one step."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_notify as kbn


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


def _chain(conn):
    """``A -> OLD -> C`` plus ``NEW`` (parent ``A``); OLD carries a tui subscription."""
    a = kb.create_task(conn, title="A", assignee="w")
    old = kb.create_task(conn, title="OLD", assignee="w", parents=[a])
    c = kb.create_task(conn, title="C", assignee="w", parents=[old])
    new = kb.create_task(conn, title="NEW", assignee="w", parents=[a])
    kbn.add_notify_sub(conn, task_id=old, platform="tui", chat_id="sess-1")
    return a, old, c, new


def _subs(conn, tid):
    return [(s["platform"], s["chat_id"]) for s in kbn.list_notify_subs(conn) if s["task_id"] == tid]


def _graph(conn, *ids):
    return {t: (kb.get_task(conn, t).status, tuple(kb.parent_ids(conn, t)), tuple(kb.child_ids(conn, t))) for t in ids}


def test_replace_moves_children_and_subscription_then_archives_old(kanban_home):
    with kbc.connect_closing() as conn:
        a, old, c, new = _chain(conn)

        out = kb.replace_task(conn, old, new)

        assert out == {"old": old, "new": new, "moved_children": [c], "subscriptions": 1}
        assert kb.parent_ids(conn, c) == [new]
        assert kb.get_task(conn, old).status == "archived"
        assert ("tui", "sess-1") in _subs(conn, new)
        assert kb.get_task(conn, c).status == "todo"
        kinds_old = [e.kind for e in kb.list_events(conn, old)]
        kinds_new = [e.kind for e in kb.list_events(conn, new)]
        assert "replaced" in kinds_old and "replaces" in kinds_new
        kinds_c = [e.kind for e in kb.list_events(conn, c)]
        assert "linked" in kinds_c and "unlinked" in kinds_c

        # C waits for NEW, not for OLD's archive.
        kb.complete_task(conn, a, summary="a")
        assert kb.get_task(conn, c).status == "todo"
        kb.complete_task(conn, new, summary="new")
        kb.recompute_ready(conn)
        assert kb.get_task(conn, c).status == "ready"


def test_replace_refuses_a_running_old_and_changes_nothing(kanban_home):
    with kbc.connect_closing() as conn:
        a, old, c, new = _chain(conn)
        kb.complete_task(conn, a, summary="a")
        kb.recompute_ready(conn)
        assert kb.claim_task(conn, old) is not None
        before = _graph(conn, a, old, c, new)

        with pytest.raises(ValueError, match="a live worker is not replaced"):
            kb.replace_task(conn, old, new)
        assert _graph(conn, a, old, c, new) == before
        assert _subs(conn, new) == []


def test_replace_refuses_a_cycle_and_moves_no_edge(kanban_home):
    with kbc.connect_closing() as conn:
        old = kb.create_task(conn, title="OLD", assignee="w")
        c1 = kb.create_task(conn, title="C1", assignee="w", parents=[old])
        c2 = kb.create_task(conn, title="C2", assignee="w", parents=[old])
        new = kb.create_task(conn, title="NEW", assignee="w", parents=[c2])
        before = _graph(conn, old, c1, c2, new)

        with pytest.raises(ValueError, match="cycle"):
            kb.replace_task(conn, old, new)
        assert _graph(conn, old, c1, c2, new) == before


def test_replace_without_children_moves_subscription_and_archives(kanban_home):
    with kbc.connect_closing() as conn:
        old = kb.create_task(conn, title="OLD", assignee="w")
        new = kb.create_task(conn, title="NEW", assignee="w")
        kbn.add_notify_sub(conn, task_id=old, platform="tui", chat_id="sess-1")

        out = kb.replace_task(conn, old, new)

        assert out["moved_children"] == [] and out["subscriptions"] == 1
        assert kb.get_task(conn, old).status == "archived"
        assert ("tui", "sess-1") in _subs(conn, new)


@pytest.mark.parametrize("case", ["same", "unknown_old", "unknown_new", "archived_new"])
def test_replace_refuses_bad_ids(kanban_home, case):
    with kbc.connect_closing() as conn:
        old = kb.create_task(conn, title="OLD", assignee="w")
        new = kb.create_task(conn, title="NEW", assignee="w")
        if case == "archived_new":
            kb.archive_task(conn, new)
        pair = {"same": (old, old), "unknown_old": ("t_nope", new),
                "unknown_new": (old, "t_nope"), "archived_new": (old, new)}[case]
        with pytest.raises(ValueError):
            kb.replace_task(conn, *pair)
        assert kb.get_task(conn, old).status != "archived"


def test_cli_replace_json_prints_the_result(kanban_home):
    with kbc.connect_closing() as conn:
        _a, old, c, new = _chain(conn)

    out = json.loads(kc.run_slash(f"replace {old} --with {new} --json"))

    assert out == {"old": old, "new": new, "moved_children": [c], "subscriptions": 1}


def test_cli_replace_without_with_is_a_usage_error(kanban_home):
    with kbc.connect_closing() as conn:
        old = kb.create_task(conn, title="OLD", assignee="w")

    out = kc.run_slash(f"replace {old}")

    assert "usage error" in out and "--with" in out


def test_replace_is_denied_to_delegated_children():
    assert "replace" in kc._DELEGATED_CHILD_DENIED_ACTIONS
