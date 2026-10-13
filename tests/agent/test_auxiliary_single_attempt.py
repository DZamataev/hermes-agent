"""Single-attempt auxiliary tasks (``_SINGLE_ATTEMPT_TASKS``, today the composer prediction).

A decorative one-shot must cost one request: ``call_llm`` neither retries the same provider on a
transient error nor walks the recovery ladder to a fallback, and its output cap is forwarded on
every route (elsewhere a cap is only forwarded where the provider requires or honours it).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import agent.auxiliary_client as aux
from agent.auxiliary_client import call_llm

_MESSAGES = [{"role": "user", "content": "hi"}]


def _client(side_effect):
    client = MagicMock()
    client.base_url = "https://openrouter.ai/api/v1"
    client.chat.completions.create.side_effect = side_effect
    return client


def _call(task, client):
    with (
        patch("agent.auxiliary_client._resolve_task_provider_model",
              return_value=("openrouter", "some-model", None, None, None)),
        patch("agent.auxiliary_client._get_cached_client", return_value=(client, "some-model")),
        patch("agent.auxiliary_client._validate_llm_response", side_effect=lambda resp, _task, **_kw: resp),
        patch("agent.auxiliary_client.time.sleep"),
    ):
        return call_llm(task=task, messages=_MESSAGES)


@pytest.mark.parametrize("error", [ConnectionError("Connection error."), TimeoutError("Request timed out.")])
def test_a_single_attempt_task_is_never_retried_or_failed_over(error):
    client = _client([error, "second answer"])

    with pytest.raises(type(error)):
        _call("composer_predictions", client)

    assert client.chat.completions.create.call_count == 1


def test_other_tasks_keep_the_same_provider_retry():
    client = _client([ConnectionError("Connection error."), "second answer"])

    assert _call("skills_hub", client) == "second answer"
    assert client.chat.completions.create.call_count == 2


def test_a_single_attempt_task_always_gets_its_output_cap():
    """A named custom endpoint is a route where a cap is normally left out."""
    route = {"provider": "custom", "model": "local-model", "messages": _MESSAGES, "max_tokens": 80,
             "base_url": "https://llm.example.internal/v1"}

    capped = aux._build_call_kwargs(**route, task="composer_predictions")
    uncapped = aux._build_call_kwargs(**route, task="skills_hub")

    assert capped.get("max_tokens", capped.get("max_completion_tokens")) == 80
    assert "max_tokens" not in uncapped and "max_completion_tokens" not in uncapped
