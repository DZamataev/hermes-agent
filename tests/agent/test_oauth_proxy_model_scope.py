"""Per-model capability overrides must survive to the wire on every consumer.

``anthropic_oauth_proxy`` is resolved per provider AND per model
(``runtime_provider_custom._lift_model_capabilities``), so two models on one relay may legitimately
disagree. The value chooses Bearer vs ``x-api-key``, the Claude Code transforms and the
session-affinity header, so a consumer that qualifies the decision by provider + endpoint only
would let one model's ``true`` lend wire authority to a model declared ``false`` (and lose a
model-level ``true`` behind a provider-level ``false``) right before the wire policy is chosen.

One endpoint, two models with opposing declarations, exercised in both directions.
"""

import json

import httpx
import pytest
import yaml

URL = "https://relay.example.com"
KEY = "opaque-relay-key"
# On this one relay: the provider level says yes, and TRUSTLESS_MODEL says no for itself.
TRUSTED_MODEL = "claude-sonnet-4-6"
TRUSTLESS_MODEL = "claude-haiku-4-6"


@pytest.fixture
def relay(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TEST_RELAY_KEY", KEY)
    for stale in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_TOKEN"):
        monkeypatch.delenv(stale, raising=False)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({
            "model": {"provider": "custom:relay", "default": TRUSTED_MODEL},
            "providers": {
                "relay": {
                    "api": URL,
                    "key_env": "TEST_RELAY_KEY",
                    "transport": "anthropic_messages",
                    "capabilities": {"anthropic_oauth_proxy": True},
                    "models": {
                        TRUSTLESS_MODEL: {"anthropic_oauth_proxy": False},
                    },
                },
                # The mirror image: provider-level deny, one model opting itself in.
                "inverse": {
                    "api": URL,
                    "key_env": "TEST_RELAY_KEY",
                    "transport": "anthropic_messages",
                    "capabilities": {"anthropic_oauth_proxy": False},
                    "models": {
                        TRUSTED_MODEL: {"anthropic_oauth_proxy": True},
                    },
                },
            },
        }),
        encoding="utf-8",
    )
    requests = []

    def send(client, request, **kwargs):
        requests.append(request)
        message = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "model": TRUSTED_MODEL,
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
        if json.loads(request.content or b"{}").get("stream"):
            events = [
                {"type": "message_start", "message": message},
                {"type": "message_stop"},
            ]
            data = "".join(
                f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events
            )
            return httpx.Response(
                200, request=request, content=data,
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, request=request, json=message)

    monkeypatch.setattr(httpx.Client, "send", send)
    return requests


def assert_oauth_wire(request, expected: bool) -> None:
    """Bearer + OAuth beta when the route's own model declares the capability, else x-api-key."""
    assert request.url.host == "relay.example.com"
    assert (request.headers.get("authorization") == f"Bearer {KEY}") is expected
    assert (request.headers.get("x-api-key") is None) is expected
    assert ("oauth-2025-04-20" in request.headers.get("anthropic-beta", "")) is expected


# ── the resolver itself ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "provider,model,expected",
    [
        ("custom:relay", TRUSTED_MODEL, True),      # provider-level true, model silent
        ("custom:relay", TRUSTLESS_MODEL, False),   # model-level false overrides true
        ("custom:inverse", TRUSTED_MODEL, True),    # model-level true overrides false
        ("custom:inverse", TRUSTLESS_MODEL, False),  # provider-level false, model silent
    ],
)
def test_declared_policy_is_model_qualified(relay, provider, model, expected):
    from agent.auxiliary_oauth import declared_oauth_proxy

    assert declared_oauth_proxy(provider, model) is expected


def test_main_runtime_is_not_inherited_across_models_on_one_endpoint(relay):
    """The live main runtime is authority for ITS model only.

    A main session on the trusted model must not lend its ``true`` to an auxiliary call routed to
    a model whose own declaration is ``false`` — same provider, same endpoint, different route.
    """
    from agent.auxiliary_oauth import runtime_oauth_proxy

    main = {
        "provider": "custom",
        "requested_provider": "custom:relay",
        "base_url": URL,
        "model": TRUSTED_MODEL,
        "capabilities": {"anthropic_oauth_proxy": True},
    }
    assert runtime_oauth_proxy(main, "custom:relay", URL, TRUSTED_MODEL) is True
    assert runtime_oauth_proxy(main, "custom:relay", URL, TRUSTLESS_MODEL) is False


def test_a_model_level_true_is_not_lost_behind_a_main_runtime_false(relay):
    """The inverse: a ``false`` main session must not mask a model that declares itself ``true``."""
    from agent.auxiliary_oauth import runtime_oauth_proxy

    main = {
        "provider": "custom",
        "requested_provider": "custom:inverse",
        "base_url": URL,
        "model": TRUSTLESS_MODEL,
        "capabilities": {"anthropic_oauth_proxy": False},
    }
    assert runtime_oauth_proxy(main, "custom:inverse", URL, TRUSTLESS_MODEL) is False
    assert runtime_oauth_proxy(main, "custom:inverse", URL, TRUSTED_MODEL) is True


# ── the same decision through the cached auxiliary client ────────────────────

@pytest.mark.parametrize(
    "provider,main_model,aux_model,expected",
    [
        # true → false: the main session's authority must not reach the denying model.
        ("relay", TRUSTED_MODEL, TRUSTLESS_MODEL, False),
        ("relay", TRUSTED_MODEL, TRUSTED_MODEL, True),
        # false → true: the denying main session must not mask the model's own opt-in.
        ("inverse", TRUSTLESS_MODEL, TRUSTED_MODEL, True),
        ("inverse", TRUSTLESS_MODEL, TRUSTLESS_MODEL, False),
    ],
)
def test_cached_auxiliary_client_carries_the_target_models_policy(
    relay, provider, main_model, aux_model, expected
):
    from agent.auxiliary_client import _get_cached_client

    main = {
        "provider": "custom",
        "requested_provider": f"custom:{provider}",
        "base_url": URL,
        "api_mode": "anthropic_messages",
        "model": main_model,
        "capabilities": {"anthropic_oauth_proxy": provider == "relay"},
    }
    client, model = _get_cached_client(f"custom:{provider}", aux_model, main_runtime=main)
    assert client is not None
    client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": "hello"}], max_tokens=32,
    )
    assert_oauth_wire(relay[-1], expected)


def test_cached_clients_do_not_leak_policy_between_two_models(relay):
    """Both models in one process, alternating: the cache must key the decision per model."""
    from agent.auxiliary_client import _get_cached_client

    main = {
        "provider": "custom",
        "requested_provider": "custom:relay",
        "base_url": URL,
        "api_mode": "anthropic_messages",
        "model": TRUSTED_MODEL,
        "capabilities": {"anthropic_oauth_proxy": True},
    }
    for aux_model, expected in (
        (TRUSTED_MODEL, True), (TRUSTLESS_MODEL, False),
        (TRUSTED_MODEL, True), (TRUSTLESS_MODEL, False),
    ):
        client, model = _get_cached_client("custom:relay", aux_model, main_runtime=main)
        client.chat.completions.create(
            model=model, messages=[{"role": "user", "content": "hello"}], max_tokens=32,
        )
        assert_oauth_wire(relay[-1], expected)


def test_session_affinity_header_follows_the_model_not_the_endpoint(relay):
    """``x-claude-code-session-id`` rides only on routes whose own model declares the capability."""
    from agent import auxiliary_client as aux
    from agent.claude_code_session import CLAUDE_CODE_SESSION_HEADER

    main = {
        "provider": "custom",
        "requested_provider": "custom:relay",
        "base_url": URL,
        "api_mode": "anthropic_messages",
        "model": TRUSTED_MODEL,
        "session_id": "20260922_120000_relay",
        "capabilities": {"anthropic_oauth_proxy": True},
    }

    def header_for(model):
        with aux.scoped_runtime_main(main):
            kwargs = aux._build_call_kwargs(
                "custom:relay", model, [{"role": "user", "content": "hi"}], base_url=URL,
            )
        return (kwargs.get("extra_headers") or {}).get(CLAUDE_CODE_SESSION_HEADER)

    # The main model's own route declares the capability; the sibling model declares it off, so no
    # header — the relay must not be told this call belongs to an OAuth-pinned conversation.
    assert header_for(TRUSTED_MODEL)
    assert header_for(TRUSTLESS_MODEL) is None


# ── a slot that arrives with its own resolved endpoint (MoA) ─────────────────

@pytest.mark.parametrize(
    "provider,model,expected",
    [
        ("relay", TRUSTED_MODEL, True),
        ("relay", TRUSTLESS_MODEL, False),
        ("custom:relay", TRUSTED_MODEL, True),
        ("inverse", TRUSTED_MODEL, True),
        ("inverse", TRUSTLESS_MODEL, False),
    ],
)
def test_moa_slot_with_resolved_endpoint_keeps_its_named_providers_policy(relay, provider, model, expected):
    """A MoA slot is sent with the base_url/api_key/api_mode its provider resolved to.

    The explicit endpoint must not flatten the named provider into anonymous ``custom``: the
    policy is looked up by provider name, so the flattened call went out without the OAuth wire
    and a relay answered 429 on every reference and aggregator call while the main session on
    the same relay and model kept working.
    """
    from agent.auxiliary_client import call_llm
    from agent.moa_loop import _slot_runtime

    runtime = _slot_runtime({"provider": provider, "model": model})
    assert runtime.get("base_url") == URL
    call_llm(
        task="moa_aggregator", messages=[{"role": "user", "content": "hello"}], max_tokens=32,
        main_runtime={"provider": "moa", "base_url": "moa://local", "model": "simple"}, **runtime,
    )
    assert_oauth_wire(relay[-1], expected)


def test_an_unrelated_explicit_endpoint_still_routes_as_custom(relay):
    """Only the provider's OWN endpoint keeps its name; another URL is a different route."""
    from agent.auxiliary_client import _resolve_task_provider_model

    assert _resolve_task_provider_model(None, "relay", TRUSTED_MODEL, URL, KEY)[0] == "relay"
    assert _resolve_task_provider_model(None, "relay", TRUSTED_MODEL, f"{URL}/", KEY)[0] == "relay"
    assert _resolve_task_provider_model(
        None, "relay", TRUSTED_MODEL, "https://elsewhere.example.com", KEY,
    )[0] == "custom"


def test_an_unknown_model_keeps_the_provider_level_policy(relay):
    """A caller that resolves no per-model entry still gets the provider's declared map."""
    from agent.auxiliary_oauth import declared_oauth_proxy

    assert declared_oauth_proxy("custom:relay", "some-unlisted-model") is True
    assert declared_oauth_proxy("custom:inverse", "some-unlisted-model") is False
    assert declared_oauth_proxy("custom:absent", TRUSTED_MODEL) is None


def test_vendor_prefixed_model_ids_compare_by_bare_name(relay):
    """``anthropic/claude-haiku-4-6`` and ``claude-haiku-4-6`` are one route, not two."""
    from agent.auxiliary_oauth import runtime_oauth_proxy

    main = {
        "provider": "custom",
        "requested_provider": "custom:relay",
        "base_url": URL,
        "model": TRUSTED_MODEL,
        "capabilities": {"anthropic_oauth_proxy": True},
    }
    assert runtime_oauth_proxy(main, "custom:relay", URL, f"anthropic/{TRUSTLESS_MODEL}") is False
    assert runtime_oauth_proxy(main, "custom:relay", URL, f"anthropic/{TRUSTED_MODEL}") is True
