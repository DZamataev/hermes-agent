"""Model-predicted next user message for the composer ghost.

Inspired by ChatGPT/Codex "Composer predictions" (OpenAI release notes, Oct 9 2026): after the
agent replies, a suggested next message appears in the empty composer; Tab puts it in the input
for review and it is never sent automatically. ``display.composer_predictions`` (default on,
Settings → Chat in Desktop) turns it off: every completed turn bills one extra, mostly cached, request.

Two paths, picked by ``auxiliary.composer_predictions``:

- **Same model (default, ``provider: auto``).** A detached cache-parity fork of the live agent
  (the ``/btw`` rail, ``build_cache_parity_fork``) replays the conversation verbatim and appends
  one prediction request, so the request is a warm prompt-cache read plus a few output tokens,
  and the model sees the whole conversation rather than a clipped digest.
- **Routed (a different provider/model pinned).** That model's cache is cold, so a short
  transcript of the last turns goes through one plain auxiliary call instead.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

TASK = "composer_predictions"
MAX_PREDICTION_CHARS = 100
MAX_PREDICTION_WORDS = 12
_CONTEXT_MESSAGES = 6
_PER_MESSAGE_CHARS = 1500
_NONE = "NONE"
# The model may spend its first iteration on a (denied) tool call; a third request is never worth it.
_FORK_MAX_ITERATIONS = 2
# A reply needs a conversation to continue: the first exchange has no pattern to predict from.
_MIN_ASSISTANT_MESSAGES = 2
# Above this many input tokens outside the prompt cache, a decorative guess is not worth its price.
_COLD_CACHE_TOKENS = 10_000
# Models echo the transcript's role labels back ("USER: ...").
_ROLE_PREFIX_RE = re.compile(r"^(?:user|assistant)\s*:\s*", re.IGNORECASE)

# The rules are shared by both paths; the heuristics follow Claude Code's prompt suggestions:
# predict the user's own next message, prefer silence, and never speak in the assistant's voice.
_RULES = (
    "Predict what the USER would type next, not what you think they should do. The test: would they "
    "think \"I was just about to type that\"?\n\n"
    "Start from the user's original request and their recent messages.\n"
    "- They asked for several steps and one is done: the next step (\"run the tests\").\n"
    "- Code was just written: \"try it out\".\n"
    "- The assistant offered options: the one this user would pick, judging by the conversation.\n"
    "- The assistant asked whether to continue: \"yes\" or \"go ahead\".\n"
    "- The task is complete with an obvious follow-up: \"commit this\", \"push it\".\n"
    "- After an error or a misunderstanding: stay silent, they need to assess it themselves.\n"
    "Be specific: \"run the tests\" beats \"continue\".\n\n"
    "Never suggest: praise or thanks (\"looks good\"), a question, anything in the assistant's voice "
    "(\"Let me...\", \"I'll...\", \"Here's...\"), a new idea the user did not ask about, or more than "
    "one sentence. Stay silent when the next step is not obvious from what the user said, or when "
    "the suggestion could be unsafe or touches credentials, private data or security incidents.\n\n"
    f"Format: 2-{MAX_PREDICTION_WORDS} words in the user's own language and style. Output only the message "
    f"text: no role label, no quotes, no explanation. To stay silent, output exactly {_NONE}."
)

_PROMPT = (
    "You predict the next message a user will type to their AI agent, from the recent conversation "
    "below. Never write the assistant's answer.\n\n" + _RULES
)

_FORK_PROMPT = (
    "[Composer prediction request from the app, not a message from the user. Do not answer the "
    "user, continue the task or call tools.]\n\n" + _RULES
)

# One-word replies that are complete messages on their own.
_SHORT_OK = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "no", "push", "commit", "deploy", "stop", "continue",
    "check", "exit", "quit", "да", "нет", "ок", "окей", "ага", "давай", "продолжай", "дальше", "стоп",
    "коммить", "закоммить", "пуш", "запушь", "деплой", "проверь",
})
_WRAPPER_TAG_RE = re.compile(r"^<(suggestion|response|output|answer|result)>(.*)</\1>$", re.IGNORECASE | re.DOTALL)
_LABEL_RE = re.compile(
    r"^\s*(?:suggested\s+(?:response|reply|input|prompt)|suggestion|response|reply|answer|output|result"
    r"|предложение|подсказка|ответ)\s*[:：]\s*", re.IGNORECASE)
_META_RE = re.compile(
    r"^nothing (?:found|to suggest)|^no suggestion|\bsilence is\b|\bstay(?:s|ing)? silent\b|^\W*silence\W*$"
    r"|^\W*(?:done|готово|молчу|молчание|нет предложени[йя]|нечего предложить)\W*$", re.IGNORECASE)
_BRACKETED_RE = re.compile(r"^(?:\(.*\)|\[.*\]|（.*）|【.*】)$", re.DOTALL)
_ERROR_PREFIXES = ("api error:", "prompt is too long", "request timed out", "invalid api key", "image was too large")
_SENTENCE_BREAK_RE = re.compile(r"[.!?]\s+[A-ZА-ЯЁ]|[。！？]\s*\w")
_EVALUATIVE_RE = re.compile(
    r"\b(?:thanks|thank you|looks good|sounds good|that works|that worked|that's all|nice|great|perfect"
    r"|makes sense|awesome|excellent|спасибо|благодарю|отлично|супер|круто|выглядит хорошо|звучит хорошо"
    r"|всё работает|все работает)\b", re.IGNORECASE)
_ASSISTANT_VOICE_RE = re.compile(
    r"^(?:let me|i'll|i've|i'm|i can|i would|i think|i notice|here's|here is|here are|that's|this is|this will"
    r"|you can|you should|you could|sure,|of course|certainly"
    r"|давайте я|сейчас я|я сейчас|я могу|я сделаю|я думаю|вот |вы можете|ты можешь|конечно,)", re.IGNORECASE)


def reject_reason(text: str) -> str | None:
    """Why a raw model reply is not a usable ghost, else ``None``. A model that ignored the task
    announces silence, praises, answers in its own voice or writes a paragraph; showing any of that
    as the user's next message is worse than showing nothing."""
    lower = text.lower()
    words = text.split()
    checks = (
        ("meta_text", lambda: bool(_META_RE.search(lower))),
        ("meta_wrapped", lambda: bool(_BRACKETED_RE.match(text))),
        ("error_message", lambda: lower.startswith(_ERROR_PREFIXES)),
        ("prefixed_label", lambda: bool(re.match(r"^\w+:\s", text))),
        ("too_few_words", lambda: len(words) < 2 and not text.startswith("/") and lower.strip(".!") not in _SHORT_OK),
        ("too_many_words", lambda: len(words) > MAX_PREDICTION_WORDS),
        ("too_long", lambda: len(text) >= MAX_PREDICTION_CHARS),
        ("multiple_sentences", lambda: bool(_SENTENCE_BREAK_RE.search(text))),
        ("has_formatting", lambda: "\n" in text or "*" in text),
        ("evaluative", lambda: bool(_EVALUATIVE_RE.search(lower))),
        ("assistant_voice", lambda: bool(_ASSISTANT_VOICE_RE.match(text))),
    )
    return next((reason for reason, hit in checks if hit()), None)


def _clean(text: str) -> str:
    """The reply as ghost text, or ``""`` when the model abstained or the reply fails a filter."""
    text = (text or "").strip()
    if (wrapped := _WRAPPER_TAG_RE.match(text)):
        text = wrapped.group(2).strip()
    text = _ROLE_PREFIX_RE.sub("", _LABEL_RE.sub("", text.strip("\"'"))).strip().strip("\"'")
    if not text or text.upper().rstrip(".") == _NONE:
        return ""
    if (reason := reject_reason(text)):
        logger.debug("composer prediction rejected (%s): %r", reason, text[:120])
        return ""
    return text


def skip_reason(history: list[dict], agent: Any = None) -> str | None:
    """Why no request should be sent at all, else ``None``: the cheapest prediction is the one
    never asked for."""
    if sum(1 for m in history if m.get("role") == "assistant") < _MIN_ASSISTANT_MESSAGES:
        return "early_conversation"
    if agent is not None and _uncached_tokens(agent) > _COLD_CACHE_TOKENS:
        return "cache_cold"
    return None


def _uncached_tokens(agent: Any) -> int:
    """Input tokens the fork would pay full price for, estimated from the parent's last request.
    Explicit cache markers (Anthropic layout) leave everything after the last breakpoint unwritten,
    so that tail plus the reply is fresh. Automatic caches keep the whole prior request, so only
    the reply is fresh, unless the last request read nothing from cache after earlier requests
    had a chance to warm it: then the provider is not caching and the whole prompt is fresh."""
    usage = getattr(agent, "_last_turn_usage", None) or {}
    fresh, reply, read = (int(usage.get(key) or 0) for key in ("input_tokens", "output_tokens", "cache_read_tokens"))
    if getattr(agent, "_use_prompt_caching", False):
        return fresh + reply
    if not read and int(getattr(agent, "session_api_calls", 0) or 0) >= 2:
        return fresh + reply
    return reply


def _task_config() -> dict[str, Any]:
    try:
        from hermes_cli.config import load_config_readonly
        task = (load_config_readonly().get("auxiliary") or {}).get(TASK)
    except Exception:  # health: allow BLE001 -- decorative: unreadable config means defaults
        return {}
    return task if isinstance(task, dict) else {}


def _predict_via_fork(agent: Any, history: list[dict], task_cfg: dict[str, Any]) -> str:
    """One prediction from a cache-parity fork of ``agent``. An empty thread tool whitelist denies
    every tool call at dispatch, so ``tools[]`` stays byte-identical for the cache but nothing runs."""
    from agent.background_review import (
        _record_review_usage_to_parent, _snapshot_review_usage, build_cache_parity_fork,
    )
    from hermes_cli.plugins import clear_thread_tool_whitelist, set_thread_tool_whitelist

    fork, _rt, _routed = build_cache_parity_fork(
        agent, task_cfg, max_iterations=_FORK_MAX_ITERATIONS, write_origin=TASK)
    try:
        set_thread_tool_whitelist(set(), deny_msg_fmt=(
            "Composer prediction denied tool call: {tool_name}. Tools are disabled here; reply with "
            "the predicted message text only."))
        result = fork.run_conversation(user_message=_FORK_PROMPT, conversation_history=list(history))
        return _clean((result or {}).get("final_response", "") or "")
    finally:
        clear_thread_tool_whitelist()
        for step in (lambda: _record_review_usage_to_parent(agent, _snapshot_review_usage(fork), task=TASK),
                     fork.shutdown_memory_provider, fork.close):
            try:
                step()
            except Exception:  # health: allow BLE001 -- teardown never raises into the RPC
                logger.debug("composer prediction fork teardown step failed", exc_info=True)


def _same_model(agent: Any, task_cfg: dict[str, Any]) -> bool:
    from agent.background_review import _resolve_review_runtime

    return not _resolve_review_runtime(agent, task_cfg).get("routed")


def _transcript(history: list[dict], coerce) -> list[str]:
    turns: list[str] = []
    for msg in history:
        role = msg.get("role")
        if role not in ("user", "assistant"):
            continue
        text = (coerce(msg.get("content")) or "").strip()
        if text:
            turns.append(f"{role.upper()}: {text[-_PER_MESSAGE_CHARS:]}")
    return turns[-_CONTEXT_MESSAGES:]


def predict_next_message(
    history: list[dict], coerce, *, main_runtime: dict | None = None, agent: Any = None,
) -> str:
    """One prediction, or ``""`` when the model abstains, fails, or the transcript has no reply yet.
    With a live ``agent`` on the session's own model the request rides its warm prompt cache."""
    turns = _transcript(history, coerce)
    # Predict only right after an agent reply; a trailing user turn means the reply never landed.
    if not turns or not turns[-1].startswith("ASSISTANT:"):
        return ""
    if (reason := skip_reason(history, agent)):
        logger.debug("composer prediction skipped: %s", reason)
        return ""
    last = history[-1] if history else {}
    # The fork replays history verbatim, so it needs a finished turn: a mid-turn tail (pending
    # tool calls, a tool result) breaks role alternation and has no reply to predict from.
    if agent is not None and last.get("role") == "assistant" and not last.get("tool_calls"):
        task_cfg = _task_config()
        try:
            if _same_model(agent, task_cfg):
                return _predict_via_fork(agent, history, task_cfg)
        except Exception:  # health: allow BLE001 -- decorative: no second billed attempt, just no ghost
            logger.debug("composer prediction fork failed", exc_info=True)
            return ""
    from agent.auxiliary_client import call_llm, extract_content_or_reasoning

    try:
        response: Any = call_llm(
            task=TASK,
            messages=[{"role": "system", "content": _PROMPT}, {"role": "user", "content": "\n\n".join(turns)}],
            max_tokens=80, temperature=None, main_runtime=main_runtime,
            reasoning_config={"enabled": False},
        )
    except Exception:  # health: allow BLE001 -- decorative: any provider/transport failure means "no ghost"
        logger.debug("composer prediction failed", exc_info=True)
        return ""
    return _clean(extract_content_or_reasoning(response) or "")
