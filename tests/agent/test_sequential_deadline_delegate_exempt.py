"""``delegate_task`` runs a nested orchestrator's whole batch inside one tool call by design, so it must not
sit under the generic sequential-call deadline: with it, every batch longer than the deadline "timed out"
while its children kept running as orphans and the orchestrator polled transcripts for hours."""
from agent import tool_executor as te

def test_delegate_task_is_exempt_from_the_sequential_deadline():
    assert "delegate_task" in te._SEQUENTIAL_DEADLINE_EXEMPT_TOOLS


def test_a_worker_waiting_for_its_orchestrator_is_not_cut_by_the_generic_deadline():
    """kanban_comment with await_reply_minutes waits up to 30 min under its own deadline and interrupt check; the
    generic 420 s cut would end the wait with tool_timeout before the reply."""
    from tools import kanban_tools as kt
    assert "kanban_comment" in te._SEQUENTIAL_DEADLINE_EXEMPT_TOOLS
    assert kt._AWAIT_MAX_MINUTES * 60 > 420
