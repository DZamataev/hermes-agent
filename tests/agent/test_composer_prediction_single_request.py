"""A composer prediction is ONE provider request on the real conversation loop.

The fork runs a single iteration with ``_no_iteration_summary`` (``tui_gateway.suggest_predict``).
Without them a truncated reply was continued with a doubled output cap, and running out of
iterations bought one more, "summarise" request: three requests for one decorative guess.
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


def _run_fork(first, *, single_request: bool = True):
    agent = _make_loop_agent()
    agent.max_tokens = predict._FORK_MAX_OUTPUT_TOKENS
    if single_request:
        agent.max_iterations = agent.iteration_budget.max_total = predict._FORK_MAX_ITERATIONS
        agent._no_iteration_summary = True
    else:
        agent.max_iterations = agent.iteration_budget.max_total = 2
    # Anything after the first response must never be consumed.
    result = _run_with_responses(agent, [first, _truncated_response(), _answer(), _answer()])
    return agent.client.chat.completions.create, result


@pytest.mark.parametrize(("first", "is_prediction"), [
    (_tool_response(500), False),     # a tool call (denied in production) earns no second request
    (_truncated_response(), False),   # a truncated reply is not continued with a larger cap
    (_answer(), True),
])
def test_prediction_fork_makes_exactly_one_provider_request(first, is_prediction):
    create, result = _run_fork(first)

    assert create.call_count == 1, f"{create.call_count} provider requests for one prediction"
    assert create.call_args.kwargs.get("max_tokens", create.call_args.kwargs.get("max_completion_tokens")) == 1024
    # Only a finished answer counts: notices and fragments come back in the same field.
    usable = bool(result.get("completed")) and not result.get("partial") and not result.get("failed")
    assert usable is is_prediction
    if is_prediction:
        assert result["final_response"] == "done"


def test_without_the_single_request_settings_a_truncated_reply_costs_three_requests():
    """The behaviour the settings above exist to prevent (two iterations, default summary)."""
    create, _ = _run_fork(_truncated_response(), single_request=False)

    assert create.call_count == 3
