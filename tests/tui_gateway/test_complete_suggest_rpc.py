"""complete.suggest: with predictions on (the default) the model goes
first (the extractor is its fallback), never carries an echoed role label, and is dropped if a new
turn moved history. A live agent on its own model predicts through a cache-parity fork that replays
history verbatim; a routed model gets the short-transcript aux call."""

from __future__ import annotations

import types

import agent.auxiliary_client as aux
import tui_gateway.server as srv


def _reply(text):
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])


def test_suggest_gating_and_prediction_fallback(monkeypatch):
    calls = []
    replies = ["USER: Open the PR too"]  # an echoed role label is stripped
    session = {"history": [], "history_version": 7, "history_lock": None, "agent": None}
    monkeypatch.setitem(srv._sessions, "s1", session)

    def fake_llm(**kw):
        calls.append(kw["task"])
        return _reply(replies[-1])

    monkeypatch.setattr(aux, "call_llm", fake_llm)

    def run(history, **display):
        session["history"] = history
        monkeypatch.setattr(srv, "_load_cfg", lambda: {"display": display})
        return srv._methods["complete.suggest"](1, {"session_id": "s1"})["result"]

    ask = [{"role": "assistant", "content": "Want me to delete it?"}, {"role": "user", "content": "no"},
           {"role": "assistant", "content": [{"type": "text", "text": "Done. Want me to push?"}]}]
    done = [{"role": "user", "content": "plan it"}, {"role": "assistant", "content": "Planned."},
            {"role": "user", "content": "fix it"}, {"role": "assistant", "content": "Fixed and pushed."}]

    assert run(ask, composer_suggestions=False, composer_predictions=True)["candidates"] == []
    assert run(ask, composer_predictions=False) == {
        "candidates": [{"text": "Yes, go ahead", "kind": "confirm"}], "history_version": 7}
    assert run(done, composer_predictions=False)["candidates"] == [] and calls == []  # the switch is honoured

    assert run(done)["candidates"] == [{"text": "Open the PR too", "kind": "prediction"}]  # on by default
    assert run(ask, composer_predictions=True)["candidates"] == [{"text": "Open the PR too", "kind": "prediction"}]
    assert calls == ["composer_predictions"] * 2

    # The first exchange has nothing to predict from: no request is sent.
    assert run(done[2:], composer_predictions=True)["candidates"] == [] and len(calls) == 2

    replies.append("NONE")
    assert run(done, composer_predictions=True)["candidates"] == []
    # The model abstained: the free extractor still answers a yes/no closer.
    assert run(ask, composer_predictions=True)["candidates"] == [{"text": "Yes, go ahead", "kind": "confirm"}]
    replies.append("Open the PR too")

    def stale_llm(**kw):
        session["history_version"] += 1  # a new turn landed mid-prediction
        return _reply(replies[-1])

    monkeypatch.setattr(aux, "call_llm", stale_llm)
    assert run(done, composer_predictions=True)["candidates"] == []


def test_prediction_rides_the_parent_cache_through_a_fork(monkeypatch):
    import agent.background_review as review
    from hermes_cli import plugins
    import tui_gateway.suggest_predict as predict

    events, recorded, rt = [], [], {"routed": False}
    history = [{"role": "user", "content": "fix it"},
               {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
               {"role": "tool", "tool_call_id": "c1", "content": "ok"},
               {"role": "assistant", "content": "Fixed and pushed."}]
    parent = types.SimpleNamespace()

    class Fork:
        def run_conversation(self, user_message, conversation_history):
            events.append(("run", user_message, conversation_history, plugins._thread_tool_whitelist.allowed))
            return {"final_response": '"Open the PR too"'}

        def shutdown_memory_provider(self):
            events.append("shutdown")

        def close(self):
            events.append("close")

    def build(agent, task_cfg, *, max_iterations, write_origin):
        events.append(("build", agent, max_iterations, write_origin))
        return Fork(), rt, False

    monkeypatch.setattr(review, "build_cache_parity_fork", build)
    monkeypatch.setattr(review, "_resolve_review_runtime", lambda agent, task_cfg=None: rt)
    monkeypatch.setattr(review, "_snapshot_review_usage", lambda fork: {"input_tokens": 9})
    monkeypatch.setattr(review, "_record_review_usage_to_parent", lambda agent, usage, *, task: recorded.append((agent, usage, task)))
    monkeypatch.setattr(aux, "call_llm", lambda **kw: (_ for _ in ()).throw(AssertionError("aux path used")))
    coerce = lambda content: content if isinstance(content, str) else ""

    assert predict.predict_next_message(history, coerce, agent=parent) == "Open the PR too"
    build_event, run_event = events[0], events[1]
    assert build_event == ("build", parent, 2, "composer_predictions")
    # History replays verbatim (tool rounds included) with every tool denied at dispatch.
    assert run_event[1] == predict._FORK_PROMPT and run_event[2] == history and run_event[3] == set()
    assert events[2:] == ["shutdown", "close"] and plugins._thread_tool_whitelist.allowed is None
    assert recorded == [(parent, {"input_tokens": 9}, "composer_predictions")]

    # A cold cache means the fork would pay for the whole prompt: no fork is built.
    class Cold:
        _use_prompt_caching = True
        _last_turn_usage = {"input_tokens": 12_000, "output_tokens": 40}

    built = len(events)
    assert predict.predict_next_message(history, coerce, agent=Cold()) == "" and len(events) == built

    # A fork failure is "no ghost", never a second billed attempt on the aux path.
    monkeypatch.setattr(review, "build_cache_parity_fork", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    assert predict.predict_next_message(history, coerce, agent=parent) == ""

    # A pinned different model has a cold cache: short transcript through the aux call instead.
    rt["routed"] = True
    monkeypatch.setattr(aux, "call_llm", lambda **kw: _reply("Ship it"))
    assert predict.predict_next_message(history, coerce, agent=parent) == "Ship it"


def test_reply_filters_and_cache_estimate():
    import tui_gateway.suggest_predict as predict

    kept = {"run the tests": "run the tests", "Suggestion: run the tests": "run the tests", "да": "да",
            "<suggestion>push it</suggestion>": "push it", "/compact": "/compact",
            "Да, покажи пример теста.": "Да, покажи пример теста."}
    assert {raw: predict._clean(raw) for raw in kept} == kept
    rejected = {"NONE": None, "Nothing to suggest": "meta_text", "(no obvious next step)": "meta_wrapped",
                "API Error: overloaded": "error_message", "hmm": "too_few_words", "word " * 13: "too_many_words",
                "Fix it. Then push": "multiple_sentences", "**run** it": "has_formatting",
                "Thanks, looks good": "evaluative", "Спасибо, отлично": "evaluative",
                "Let me check the logs": "assistant_voice", "Я сейчас проверю логи": "assistant_voice"}
    for raw, reason in rejected.items():
        assert predict._clean(raw) == "", raw
        assert reason is None or predict.reject_reason(raw.strip()) == reason, raw

    agent = types.SimpleNamespace(
        _use_prompt_caching=True, session_api_calls=5,
        _last_turn_usage={"input_tokens": 9_000, "output_tokens": 500, "cache_read_tokens": 40_000})
    assert predict._uncached_tokens(agent) == 9_500  # explicit markers: the unwritten tail + the reply
    agent._use_prompt_caching = False
    assert predict._uncached_tokens(agent) == 500    # automatic cache: only the reply is new
    agent._last_turn_usage["cache_read_tokens"] = 0
    assert predict._uncached_tokens(agent) == 9_500  # a provider that never serves a read caches nothing
    agent.session_api_calls = 1
    assert predict._uncached_tokens(agent) == 500    # first request: nothing could have been read yet
