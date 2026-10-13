"""A composer prediction is ONE provider request on the real conversation loop.

The fork runs with a provider-request budget of one (``tui_gateway.suggest_predict``), enforced
where the loop issues requests (``agent.turn_api_call.perform_api_call``). Without it a truncated
reply was continued with a doubled output cap, a truncated tool call or a transport error was
retried, and running out of iterations bought one more, "summarise" request.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import tui_gateway.suggest_predict as predict
from tests.agent.test_background_review_input_budget import (
    _final_response, _make_loop_agent, _run_with_responses, _tool_response,
)


def _usage(prompt_tokens: int = 500) -> SimpleNamespace:
    return SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=5, total_tokens=prompt_tokens + 5)


def _truncated_response() -> SimpleNamespace:
    message = SimpleNamespace(content="Run the te", reasoning_content=None, reasoning=None, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="length")], model="test/model", usage=_usage())


def _answer() -> SimpleNamespace:
    response = _final_response()
    response.usage = _usage()
    return response


def _truncated_tool_call() -> SimpleNamespace:
    """A tool call whose JSON arguments were cut off by the output limit."""
    call = SimpleNamespace(
        id="call_1", type="function", function=SimpleNamespace(name="web_search", arguments='{"query": "unfin'))
    message = SimpleNamespace(content=None, reasoning_content=None, reasoning=None, tool_calls=[call])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="length")], model="test/model", usage=_usage())


def _run_fork(first, *, single_request: bool = True):
    agent = _make_loop_agent()
    agent.max_tokens = predict._FORK_MAX_OUTPUT_TOKENS
    agent.max_iterations = agent.iteration_budget.max_total = predict._FORK_MAX_ITERATIONS
    if single_request:
        agent._provider_request_budget = predict._FORK_PROVIDER_REQUESTS
    # Anything after the first response must never be consumed.
    result = _run_with_responses(agent, [first, _truncated_response(), _answer(), _answer(), _answer()])
    return agent.client.chat.completions.create, result


def _usable(result) -> bool:
    """The condition ``_predict_via_fork`` puts on a result before reading its text."""
    return bool(result.get("completed")) and not result.get("partial") and not result.get("failed")


@pytest.mark.parametrize(("first", "is_prediction"), [
    (_tool_response(500), False),     # a tool call (denied in production) earns no second request
    (_truncated_response(), False),   # a truncated reply is not continued with a larger cap
    (_truncated_tool_call(), False),  # nor is a truncated tool call retried with one
    (TimeoutError("timed out"), False),
    (RuntimeError("HTTP 503: upstream unavailable"), False),
    (_answer(), True),
])
def test_prediction_fork_makes_exactly_one_provider_request(first, is_prediction):
    create, result = _run_fork(first)

    assert create.call_count == 1, f"{create.call_count} provider requests for one prediction"
    assert create.call_args.kwargs.get("max_tokens", create.call_args.kwargs.get("max_completion_tokens")) == 1024
    # Only a finished answer counts: notices and fragments come back in the same field.
    assert _usable(result) is is_prediction
    if is_prediction:
        assert result["final_response"] == "done"


@pytest.mark.parametrize("first", [_truncated_response(), _truncated_tool_call(), TimeoutError("timed out")])
def test_without_the_request_budget_recovery_issues_more_requests(first):
    """The behaviour the budget exists to prevent, on the same single-iteration agent."""
    create, _ = _run_fork(first, single_request=False)

    assert create.call_count > 1


def test_an_ordinary_agent_has_no_request_budget():
    from agent.turn_api_call import provider_request_budget_spent

    agent = _make_loop_agent()
    agent._provider_requests_made = 99
    assert not provider_request_budget_spent(agent)
