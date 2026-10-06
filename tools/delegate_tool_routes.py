"""Per-task route planning for ``delegate_task``: which provider/model/effort each child runs on.

Precedence for model-facing calls: the session's forced subagent route (Desktop composer) → the task's
difficulty ``tier`` → ``normal`` (the base ``delegation.*`` block) → the parent's own route. Effort follows the
same owner, with a task's own ``reasoning_effort`` between the forced route and the tier. Internal callers that
pass their own route (``credentials_cfg``, e.g. /review → auxiliary.review) keep it: tiers and the session route
re-route only what the model spawns.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

_ROUTE_FIELDS = ("provider", "model", "reasoning_effort")


def _session_route(parent_agent: Any) -> Optional[Dict[str, str]]:
    """The parent session's forced subagent route, or None when the session is on Auto."""
    raw = getattr(parent_agent, "_delegation_override", None)
    if not isinstance(raw, dict):
        return None
    route = {k: str(raw.get(k) or "").strip() for k in _ROUTE_FIELDS}
    return route if route["model"] else None


def _parse_task_effort(raw: Any, index: int) -> Any:
    """A task's ``reasoning_effort`` as the raw value the child runtime parses; None = not set."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    from hermes_constants import parse_reasoning_effort
    if parse_reasoning_effort(raw) is None:
        from hermes_constants import VALID_REASONING_EFFORTS
        raise ValueError(
            f"Task {index}: invalid reasoning_effort {raw!r}. Expected one of: none, {', '.join(VALID_REASONING_EFFORTS)}."
        )
    return raw


def _plan_task_routes(
    task_list: List[Dict[str, Any]], cfg: dict, credentials_cfg: Optional[dict], parent_agent: Any,
) -> List[Dict[str, Any]]:
    """One ``{"creds", "routing_cfg", "effort"}`` per task, all resolved up front (ValueError = refuse the call).

    ``effort`` is None when the child should take the routing block's / config's own level (existing behaviour)."""
    from tools.delegate_tool import _resolve_delegation_credentials
    from tools.delegate_tool_config import _merge_tier_config, _normalize_tier, _routing_cfg_for_tier

    forced = _session_route(parent_agent) if credentials_cfg is None else None
    plans: List[tuple] = []
    for i, task in enumerate(task_list):
        try:
            tier = _normalize_tier(task.get("tier"))
        except ValueError as exc:
            raise ValueError(f"Task {i}: {exc}") from None
        effort = _parse_task_effort(task.get("reasoning_effort"), i)
        if credentials_cfg is not None:
            plans.append(("internal", credentials_cfg, None))
        elif forced is not None:
            # A forced route is the whole answer: tier and the model's own effort pick are ignored.
            route = {k: v for k, v in forced.items() if v}
            plans.append(("forced", _merge_tier_config(cfg, route), forced["reasoning_effort"] or None))
        else:
            routing_cfg = _routing_cfg_for_tier(cfg, tier)
            plans.append((tier, routing_cfg, effort if effort is not None else routing_cfg.get("reasoning_effort")))

    resolved: Dict[str, Dict[str, Any]] = {}  # one credential resolution per distinct route (tier / forced / internal)
    routes = []
    for label, routing_cfg, effort in plans:
        if label not in resolved:
            resolved[label] = _resolve_delegation_credentials(routing_cfg, parent_agent)
        routes.append({"creds": resolved[label], "routing_cfg": routing_cfg, "effort": effort})
    return routes


def _batch_creds_summary(routes: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Call-level route metadata (live-transcript manifest, async registry): the shared route, or the distinct
    models joined when tasks run on different ones."""
    first = dict(routes[0]["creds"])
    models = list(dict.fromkeys(str(r["creds"].get("model") or "") for r in routes))
    providers = list(dict.fromkeys(str(r["creds"].get("provider") or "") for r in routes))
    if len(models) > 1:
        first["model"] = ", ".join(m or "(parent)" for m in models)
    if len(providers) > 1:
        first["provider"] = ", ".join(p or "(parent)" for p in providers)
    return first
