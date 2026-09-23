"""Keep a main session's OAuth-proxy capability scoped to its auxiliary route.

``anthropic_oauth_proxy`` is resolved per provider AND per model
(``runtime_provider_custom._lift_model_capabilities`` lets
``providers.<name>.models.<model>.capabilities`` override the provider-level map), so two models
on one relay may legitimately disagree. The value decides Bearer vs ``x-api-key``, the Claude Code
beta/system/tool transforms, thinking-signature replay and the session-affinity header, so a
decision qualified only by provider + endpoint would let a main model's ``true`` lend wire
authority to an auxiliary model declared ``false`` (and lose a model-level ``true`` behind a
provider-level ``false``) immediately before the wire policy is chosen.

Hence: a live main runtime is inherited only when the auxiliary route is the SAME route —
same provider, same endpoint, same effective model. Anything else consumes what that route's own
model-qualified configuration declares, and a route declaring nothing gets nothing.
"""

from typing import Any, Dict, Optional

from hermes_cli.route_identity import normalize_route_base_url


def _bare_model(model: Any) -> str:
    """A model id comparable across routes: no ``vendor/`` prefix, case- and space-insensitive."""
    text = str(model or "").strip().lower()
    return text.rsplit("/", 1)[-1] if text else ""


def declared_route_capabilities(provider: Any, model: Any) -> Dict[str, bool]:
    """The capability map *provider*'s config entry declares for *model* (``{}`` when none).

    Resolved by the canonical owner, so the provider-level map and its per-model override merge
    exactly as the main runtime resolves them — never a second interpretation of the same config.
    A ``vendor/model`` id matches a bare ``models:`` key (and the reverse): aggregator-prefixed and
    native spellings of one model are one route, and the prefix must not silently fall the lookup
    back to the provider-level value.
    """
    try:
        from hermes_cli.runtime_provider_custom import _get_named_custom_provider, _lift_model_capabilities
        entry = _get_named_custom_provider(str(provider or ""))
        if not isinstance(entry, dict):
            return {}
        result: Dict[str, Any] = {}
        _lift_model_capabilities(entry, _entry_model_key(entry, model), result)
        capabilities = result.get("capabilities")
        return capabilities if isinstance(capabilities, dict) else {}
    except Exception:  # noqa: BLE001 — a config read must never break client construction
        return {}


def _entry_model_key(entry: Dict[str, Any], model: Any) -> Optional[str]:
    """The ``models:`` key of *entry* naming *model*, else *model* unchanged."""
    name = str(model or "").strip()
    if not name:
        return None
    models = entry.get("models")
    if not isinstance(models, dict) or name in models:
        return name
    bare = _bare_model(name)
    return next((key for key in models if _bare_model(key) == bare), name)


def declared_oauth_proxy(provider: Any, model: Any) -> Optional[bool]:
    """``anthropic_oauth_proxy`` as *provider*'s entry declares it for *model*, else None."""
    value = declared_route_capabilities(provider, model).get("anthropic_oauth_proxy")
    return value if isinstance(value, bool) else None


def _inherited_oauth_proxy(main_runtime: Any, provider: Any, base_url: Any, model: Any) -> Optional[bool]:
    """The main runtime's value when the auxiliary route is that exact route, else None."""
    if not isinstance(main_runtime, dict):
        return None
    capabilities = main_runtime.get("capabilities")
    if not isinstance(capabilities, dict) or not isinstance(
        capabilities.get("anthropic_oauth_proxy"), bool
    ):
        return None
    runtime_base = main_runtime.get("base_url")
    if not base_url or not runtime_base:
        return None
    if normalize_route_base_url(base_url) != normalize_route_base_url(runtime_base):
        return None
    # A different model on the same endpoint is a different route for this decision: its own
    # declaration owns it. An unknown target model cannot be proven different, so it still
    # inherits — that is the pre-existing shape for callers that resolve no concrete model.
    target_model = _bare_model(model)
    if target_model and target_model != _bare_model(main_runtime.get("model")):
        return None
    target = str(provider or "").lower().removeprefix("custom:")
    source = (
        str(
            main_runtime.get("requested_provider") or main_runtime.get("provider") or ""
        )
        .lower()
        .removeprefix("custom:")
    )
    if target == source or target in {"auto", "main", "custom"}:
        return capabilities["anthropic_oauth_proxy"]
    return None


def runtime_oauth_proxy(
    main_runtime: Any, provider: Any, base_url: Any, model: Any = None,
) -> Optional[bool]:
    """OAuth-proxy policy for one auxiliary route, or None when nothing declares it.

    Inherits the main session's live value only for its own provider + endpoint + model; any other
    route — including a different model on the same relay — answers from its own model-qualified
    declaration, so a pin can neither borrow nor lose wire authority across models.

    A declaration belongs to the provider's OWN endpoint. The same name pointed at another origin
    (``auxiliary.<task>.base_url``, a ``fallback_chain`` entry) is a different server: it gets the
    entry's key if the user composed it so, but never the Claude Code identity and Bearer-as-OAuth
    policy the relay declared for itself. An empty *base_url* means the entry's own endpoint.
    """
    inherited = _inherited_oauth_proxy(main_runtime, provider, base_url, model)
    if inherited is not None:
        return inherited
    if base_url:
        from hermes_cli.route_identity import named_provider_owns_endpoint
        if not named_provider_owns_endpoint(provider, base_url):
            return None
    return declared_oauth_proxy(provider, model)
