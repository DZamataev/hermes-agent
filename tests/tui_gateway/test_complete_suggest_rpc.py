"""complete.suggest: with predictions on (the default) the model goes
first (the extractor is its fallback), never carries an echoed role label, and is dropped if a new
turn moved history. A live agent on its own model predicts through a cache-parity fork that replays
history verbatim; a routed model gets the short-transcript aux call."""

from __future__ import annotations

import threading
import types

import pytest

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


def _fork_harness(monkeypatch, *, task_cfg=None, run=None, routed=False):
    """A parent agent plus a fake cache-parity fork; returns ``(predict, parent, events, recorded)``."""
    import agent.background_review as review
    from hermes_cli import plugins
    import tui_gateway.suggest_predict as predict

    events, recorded = [], []
    parent = types.SimpleNamespace(
        provider="custom", requested_provider="teamclaude", model="main-model", base_url="https://main.example/v1",
        api_key="main-key",
        _active_children=[], _active_children_lock=threading.Lock(), interrupted=threading.Event())

    class Fork:
        max_tokens = 16_000

        def run_conversation(self, user_message, conversation_history):
            events.append(("run", user_message, conversation_history, plugins._thread_tool_whitelist.allowed,
                           self.max_tokens, self in parent._active_children,
                           (self._provider_request_budget, self.compression_enabled)))
            return run(self) if run else {"final_response": '"Open the PR too"', "completed": True}

        def interrupt(self, message=None, **kw):
            events.append(("interrupt", kw.get("tool_reason")))
            parent.interrupted.set()

        def shutdown_memory_provider(self):
            events.append("shutdown")

        def close(self):
            events.append("close")

    def build(agent, cfg, *, max_iterations, write_origin):
        events.append(("build", agent, max_iterations, write_origin, dict(cfg)))
        return Fork(), {"routed": routed}, routed

    monkeypatch.setattr(review, "build_cache_parity_fork", build)
    monkeypatch.setattr(review, "_snapshot_review_usage", lambda fork: {"input_tokens": 9})
    monkeypatch.setattr(review, "_record_review_usage_to_parent", lambda agent, usage, *, task: recorded.append((agent, usage, task)))
    monkeypatch.setattr(predict, "_task_config", lambda: dict(task_cfg or {}))
    # The route is decided by the real auxiliary resolver, reading this same task config.
    monkeypatch.setattr(aux, "_get_auxiliary_task_config", lambda task: dict(task_cfg or {}))
    monkeypatch.setattr(aux, "_scoped_key_env", lambda name: {"OTHER_KEY": "other-account", "MAIN_KEY": "main-key"}.get(name, ""))
    return predict, parent, events, recorded


_HISTORY = [{"role": "user", "content": "fix it"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
            {"role": "assistant", "content": "Fixed and pushed."}]


def _coerce(content):
    return content if isinstance(content, str) else ""


def test_prediction_rides_the_parent_cache_through_a_fork(monkeypatch):
    from hermes_cli import plugins

    predict, parent, events, recorded = _fork_harness(monkeypatch)
    monkeypatch.setattr(aux, "call_llm", lambda **kw: (_ for _ in ()).throw(AssertionError("aux path used")))

    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent) == "Open the PR too"
    assert events[0] == ("build", parent, 1, "composer_predictions", {})
    _, prompt, replayed, whitelist, output_cap, tracked, spend = events[1]
    # History replays verbatim (tool rounds included) with every tool denied at dispatch.
    assert prompt == predict._FORK_PROMPT and replayed == _HISTORY and whitelist == set()
    # The guess never inherits the session's output budget, and stopping the session reaches it.
    assert output_cap == predict._FORK_MAX_OUTPUT_TOKENS and tracked
    # One provider request in total, and no compaction request on the side.
    assert spend == (1, False)
    assert events[2:] == ["shutdown", "close"] and plugins._thread_tool_whitelist.allowed is None
    assert parent._active_children == [] and recorded == [(parent, {"input_tokens": 9}, "composer_predictions")]

    # A cold cache means the fork would pay for the whole prompt: no fork is built.
    parent._use_prompt_caching, parent._last_turn_usage = True, {"input_tokens": 12_000, "output_tokens": 40}
    built = len(events)
    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent) == "" and len(events) == built
    parent._last_turn_usage = {}

    # A fork failure is "no ghost", never a second billed attempt on the aux path.
    import agent.background_review as review
    monkeypatch.setattr(review, "build_cache_parity_fork", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent) == ""


@pytest.mark.parametrize("task_cfg", [
    {"provider": "auto", "model": "cheap-model"},                    # a model without a provider
    {"provider": "openrouter"},                                      # a provider without a model
    {"provider": "teamclaude", "model": "main-model", "base_url": "https://other.example/v1"},  # another endpoint
    {"provider": "teamclaude", "model": "main-model", "api_key": "other-account"},              # another account
    {"provider": "teamclaude", "key_env": "OTHER_KEY"},                                         # ... named by env var
    {"provider": "teamclaude", "api_key_env": "OTHER_KEY"},
])
def test_any_pinned_route_uses_the_short_transcript_call(monkeypatch, task_cfg):
    """Each routing key counts on its own, and the session's cold cache is not the routed model's
    problem: it gets six turns, not the conversation."""
    predict, parent, events, _ = _fork_harness(monkeypatch, task_cfg=task_cfg)
    parent._use_prompt_caching, parent._last_turn_usage = True, {"input_tokens": 12_000, "output_tokens": 40}
    monkeypatch.setattr(aux, "call_llm", lambda **kw: _reply("Ship it"))

    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent) == "Ship it" and events == []


@pytest.mark.parametrize("task_cfg", [
    {}, {"provider": "auto", "model": ""}, {"provider": "teamclaude", "model": "main-model"},
    {"provider": "custom", "base_url": "https://main.example/v1/"}, {"model": "main-model", "api_key": "main-key"},
    {"provider": "teamclaude", "key_env": "MAIN_KEY"},
    # A bare credential under provider auto is dropped by the auxiliary router, so the short call
    # would run on the session's account anyway: nothing is routed elsewhere.
    {"api_key": "other-account"}, {"provider": "auto", "key_env": "OTHER_KEY"},
])
def test_unrouted_task_config_stays_on_the_session_model(monkeypatch, task_cfg):
    predict, parent, events, _ = _fork_harness(monkeypatch, task_cfg=task_cfg)

    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent) == "Open the PR too" and events


@pytest.mark.parametrize(("task_cfg", "stale"), [({}, lambda: True), ({"timeout": 0.05}, None)])
def test_superseded_or_slow_prediction_is_interrupted(monkeypatch, task_cfg, stale):
    """A reply nobody will see is stopped, not merely dropped: a new turn, or the task timeout."""
    def blocked(fork):
        assert parent.interrupted.wait(5), "the watcher never interrupted the fork"
        return {"final_response": "", "completed": False, "interrupted": True}

    predict, parent, events, _ = _fork_harness(monkeypatch, task_cfg=task_cfg, run=blocked)
    monkeypatch.setattr(predict, "_WATCH_INTERVAL_SECONDS", 0.01)

    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent, stale=stale) == ""
    assert ("interrupt", "composer prediction superseded") in events and parent._active_children == []


def test_fork_never_inherits_the_task_routing_keys(monkeypatch):
    """The session's provider is known under two names (``custom`` / ``teamclaude``). Naming the
    alias in the task config is still the session's own model, and the fork's runtime resolver,
    which knows only one of the names, must not get the chance to disagree."""
    task_cfg = {"provider": "teamclaude", "model": "main-model", "api_key": "main-key", "timeout": 30,
                "max_input_tokens": 9}
    predict, parent, events, _ = _fork_harness(monkeypatch, task_cfg=task_cfg)

    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent) == "Open the PR too"
    assert events[0][4] == {"timeout": 30, "max_input_tokens": 9}


def test_a_routed_fork_is_never_fed_the_conversation(monkeypatch):
    """Should the builder still resolve another model, its fork has none of the parent's prompt
    or tools: replaying the whole conversation into it would be a cold, full-price request."""
    predict, parent, events, _ = _fork_harness(monkeypatch, routed=True)
    monkeypatch.setattr(aux, "call_llm", lambda **kw: (_ for _ in ()).throw(AssertionError("second attempt")))

    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent) == ""
    assert [e for e in events if isinstance(e, tuple) and e[0] == "run"] == [] and events[-2:] == ["shutdown", "close"]


@pytest.mark.parametrize(("reply", "reasoning"), [
    ("x" * 300, ""),      # a reply longer than any ghost
    ("", "y" * 3500),     # ~1170 tokens of reasoning at three characters a token
    ("", "思" * 1100),    # an unspaced script is about a token per character
])
def test_runaway_output_is_cut_off_on_our_side(monkeypatch, reply, reasoning):
    """Not every transport honours the output limit, so the stream is counted too: a reply longer
    than any ghost, or reasoning past the cap, interrupts the request and yields nothing."""
    def stream(fork):
        for chunk in (reasoning[i:i + 500] for i in range(0, len(reasoning), 500)):
            fork.reasoning_callback(chunk)
        for chunk in (reply[i:i + 50] for i in range(0, len(reply), 50)):
            fork.stream_delta_callback(chunk)
        fork.stream_delta_callback(None)
        return {"final_response": "Open the PR too", "completed": True}

    predict, parent, events, _ = _fork_harness(monkeypatch, run=stream)

    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent) == ""
    assert events.count(("interrupt", "composer prediction over its output cap")) == 1


@pytest.mark.parametrize("result", [
    {"final_response": "⚠️ No reply: the turn stopped.", "completed": False, "failed": True},
    {"final_response": "Run the te", "completed": True, "partial": True},
    {"final_response": None, "completed": False},
])
def test_only_a_finished_answer_is_a_prediction(monkeypatch, result):
    predict, parent, _, _ = _fork_harness(monkeypatch, run=lambda fork: result)

    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent) == ""


def test_a_moa_session_gets_no_model_prediction(monkeypatch):
    """One mixture-of-agents turn is several requests (advisors, then the aggregator), all issued
    before the request budget is consulted: there is no one-request prediction on it."""
    predict, parent, events, _ = _fork_harness(monkeypatch)
    parent.provider = parent.requested_provider = "moa"
    monkeypatch.setattr(aux, "call_llm", lambda **kw: (_ for _ in ()).throw(AssertionError("aux path used")))

    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent) == "" and events == []


@pytest.mark.parametrize("reasoning", ["Considering what the user asked for earlier. " * 49, "思考" * 500])
def test_output_estimate_does_not_depend_on_how_the_stream_is_sliced(reasoning):
    """Rounding each delta on its own made the same reasoning pass in one chunk and trip in
    four-character chunks; the estimate is taken of everything streamed so far."""
    import tui_gateway.suggest_predict as predict

    verdicts = set()
    for size in (1, 2, 3, 4, 5, 8, 64, len(reasoning)):
        fork = types.SimpleNamespace(interrupt=lambda **kw: None)
        guard = predict._OutputGuard(fork)
        for start in range(0, len(reasoning), size):
            guard.on_reasoning(reasoning[start:start + size])
        guard.on_reply("run the tests")
        verdicts.add(guard.tripped)
    assert verdicts == {False}


def test_reasoning_under_the_cap_is_left_alone(monkeypatch):
    def stream(fork):
        fork.reasoning_callback("y" * 2400)  # ~800 tokens
        fork.stream_delta_callback("Open the PR too")
        return {"final_response": "Open the PR too", "completed": True}

    predict, parent, events, _ = _fork_harness(monkeypatch, run=stream)

    assert predict.predict_next_message(_HISTORY, _coerce, agent=parent) == "Open the PR too"
    assert not [e for e in events if isinstance(e, tuple) and e[0] == "interrupt"]


def test_a_turn_admitted_right_after_the_snapshot_is_seen_as_new(monkeypatch):
    """History, version and the in-flight turn are read under ``history_lock``, which admission
    holds too: a turn that starts the moment the lock is released cannot be taken for the turn
    the prediction started from."""
    import tui_gateway.suggest_predict as predict

    done = [{"role": "user", "content": "plan it"}, {"role": "assistant", "content": "Planned."},
            {"role": "user", "content": "fix it"}, {"role": "assistant", "content": "Fixed and pushed."}]
    session = {"history": done, "history_version": 7, "agent": None, "inflight_turn": None}

    class AdmitOnRelease:
        """Stands in for ``history_lock``: a new turn is admitted as soon as the reader lets go."""
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            session["inflight_turn"] = {"started_at": 300.0}

    session["history_lock"] = AdmitOnRelease()
    monkeypatch.setitem(srv._sessions, "s3", session)
    monkeypatch.setattr(srv, "_load_cfg", lambda: {"display": {}})
    seen = []
    monkeypatch.setattr(predict, "predict_next_message",
                        lambda history, coerce, *, main_runtime=None, agent=None, stale=None: seen.append(stale()) or "x y")

    assert srv._methods["complete.suggest"](1, {"session_id": "s3"})["result"]["candidates"] == [] and seen == [True]

    # A turn record without a timestamp is still a turn that was not there at the start.
    session.update(history_lock=None, inflight_turn=None)
    monkeypatch.setattr(predict, "predict_next_message",
                        lambda history, coerce, *, main_runtime=None, agent=None, stale=None:
                        session.update(inflight_turn={"assistant": "", "streaming": True}) or "x y")
    assert srv._methods["complete.suggest"](1, {"session_id": "s3"})["result"]["candidates"] == []


def test_a_new_turn_supersedes_the_prediction_before_history_moves(monkeypatch):
    """Admission starts a fresh in-flight turn at once; ``history_version`` only moves when that
    turn's result is committed. Both cancel the request and drop its result."""
    import tui_gateway.suggest_predict as predict

    done = [{"role": "user", "content": "plan it"}, {"role": "assistant", "content": "Planned."},
            {"role": "user", "content": "fix it"}, {"role": "assistant", "content": "Fixed and pushed."}]
    # The finished turn may still be marked in flight when the client asks: that is not a new turn.
    session = {"history": done, "history_version": 7, "history_lock": None, "agent": None,
               "inflight_turn": {"started_at": 100.0}}
    monkeypatch.setitem(srv._sessions, "s2", session)
    monkeypatch.setattr(srv, "_load_cfg", lambda: {"display": {}})
    seen = {}

    def fake_predict(history, coerce, *, main_runtime=None, agent=None, stale=None):
        seen["before"] = stale()
        session.update(on_predict)
        seen["after"] = stale()
        return "Open the PR too"

    monkeypatch.setattr(predict, "predict_next_message", fake_predict)
    def run():
        return srv._methods["complete.suggest"](1, {"session_id": "s2"})["result"]["candidates"]

    on_predict = {"inflight_turn": None}  # the old turn finished clearing
    assert run() == [{"text": "Open the PR too", "kind": "prediction"}] and seen == {"before": False, "after": False}

    on_predict = {"inflight_turn": {"started_at": 200.0}}  # prompt.submit admitted a new turn
    assert run() == [] and seen == {"before": False, "after": True}


def test_reply_filters_and_cache_estimate():
    import tui_gateway.suggest_predict as predict

    kept = {"run the tests": "run the tests", "Suggestion: run the tests": "run the tests", "да": "да",
            "покажи": "покажи", "运行测试": "运行测试", "テストを実行して": "テストを実行して",
            "继续": "继续", "提交": "提交", "해줘": "해줘", "停": "停", "네": "네", "可": "可", "否": "否",
            "fix a[0] and b[1]": "fix a[0] and b[1]", "покажи x < y": "покажи x < y",
            "<suggestion>push it</suggestion>": "push it", "/compact": "/compact",
            "Да, покажи пример теста.": "Да, покажи пример теста."}
    assert {raw: predict._clean(raw) for raw in kept} == kept
    rejected = {"NONE": None, "Nothing to suggest": "meta_text", "(no obvious next step)": "meta_wrapped",
                "API Error: overloaded": "error_message", "парсер в файле [путь к файлу]": "placeholder",
                "open <file name> please": "placeholder", "hmm": "too_few_words", "呃": "too_few_words", "测试" * 13: "too_many_words", "word " * 13: "too_many_words",
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
