# Delegate model routing (tiers + session override) Implementation Plan

> Executed inline by the agent that wrote it (TDD per task, commit per task). Steps use `- [ ]`.

**Goal:** Let `delegate_task` children run on a model/effort chosen per task (difficulty tier), per task effort, or forced per session from the Desktop composer — without changing behaviour for anyone who configures nothing.

**Architecture:** The model picks a fixed-name difficulty tier (`easy | normal | hard`) and optionally a `reasoning_effort` per task; the operator maps tiers to provider/model/effort in config (`delegation.*` = normal, `delegation.tiers.easy|hard`). A Desktop session can force one provider/model/effort for all its children; that override lives on the session (not config), beats every other source, and survives resume. Config is still read on every call (no snapshot).

**Tech Stack:** Python (`tools/delegate_tool*.py`, `tui_gateway/`), Electron/React desktop (`apps/desktop/src`), pytest via `scripts/run_tests.sh`, vitest from `apps/desktop`.

**Prior art:** upstream PR #34650 (tier set `small|medium|large`, top-level only, closed "We do not want this") — its merge semantics (`_merge_delegation_config_for_tier`) are reused; PR #77953 (free-form per-task model/provider) — rejected approach for the model-facing schema.

## Global Constraints

- Tier names: exactly `easy`, `normal`, `hard`. Omitted/blank tier = `normal`.
- `normal` = the existing `delegation.provider/model/base_url/api_key/api_mode/reasoning_effort` (no migration). `easy`/`hard` = `delegation.tiers.easy|hard`, merged over the base block; an unconfigured tier falls back to `normal`.
- Precedence per child: session override (Desktop composer) → task `tier` → `normal` (`delegation.*`) → parent model. Effort: session override effort → task `reasoning_effort` → tier/`delegation.reasoning_effort` → parent.
- Schema is static (enum lists only) — nothing in the tool schema depends on config (prompt-cache invariant).
- The model never names a provider or model id. No free-form `model`/`provider` field in the model-facing schema.
- Delegation config is read on every `delegate_task` call (existing behaviour); no per-session snapshot.
- Session override is session-scoped only: never written to config.yaml.
- Desktop: no "Update delegation config" checkbox. Tier editing lives in Settings.
- All desktop copy goes through i18n; every locale file gets the keys (catalog-completeness test).

---

### Task 1: Tier config resolution (backend, pure)

**Files:**
- Modify: `tools/delegate_tool_config.py` (new helpers next to `_load_config`)
- Modify: `hermes_cli/config_defaults.py` (`delegation.reasoning_effort: ""`, `delegation.tiers: {easy: {}, hard: {}}`)
- Test: `tests/tools/test_delegate_tiers.py` (new)

**Interfaces (produces):**
- `DELEGATION_TIERS = ("easy", "normal", "hard")`
- `_normalize_tier(value: Any) -> str` — blank/None → `"normal"`; case-insensitive; unknown → `ValueError("Invalid delegation tier ...")`.
- `_merge_tier_config(base: dict, tier_cfg: dict) -> dict` — #34650 semantics: blank strings ignored; a tier that changes `provider` or `base_url` drops the inherited route bundle (`model, provider, base_url, api_key, api_mode, command, args`) before applying; other keys overlay.
- `_routing_cfg_for_tier(cfg: dict, tier: str) -> dict` — `normal` or missing/non-dict tier block → `cfg` unchanged (same object); else merged copy.

Tests: normalize (blank, case, unknown); merge (same provider keeps base url/key; provider switch clears bundle; base_url switch clears; blank overrides ignored; reasoning_effort overlay); routing (normal returns base; missing tier falls back; configured tier merges; `tiers` key not a dict → base).

### Task 2: Per-task tier + reasoning_effort in delegate_task

**Files:**
- Modify: `tools/delegate_tool.py` (`delegate_task`, `_build_children`, `_build_child_agent`, schema, description tail)
- Modify: `tools/delegate_tool_config.py` (`_resolve_child_runtime` gains `reasoning_effort_override`)
- Test: `tests/tools/test_delegate_tiers.py`

**Interfaces:**
- Consumes Task 1 helpers.
- Schema: `tasks.items.properties.tier` (enum easy/normal/hard) and `tasks.items.properties.reasoning_effort` (enum of the shared effort scale incl. `none`), both optional, static text.
- `delegate_task` validates every task's tier/effort and resolves credentials for each distinct tier BEFORE building any child (no orphan children on a bad tier). Invalid → `tool_error`.
- `_build_children(..., creds_by_task: List[dict], routing_by_task: List[dict], effort_by_task: List[Optional[str]])` — per-task overrides.
- `_build_child_agent(..., delegation_cfg: Optional[dict] = None, reasoning_effort_override: Optional[str] = None)`; when `delegation_cfg` given it replaces the `_load_config()` read for reasoning/compression.
- `_resolve_child_runtime(..., reasoning_effort_override=None)` — beats `delegation_cfg["reasoning_effort"]`; unknown value → warning + fall through.
- Internal callers passing `credentials_cfg` (e.g. `/review`) keep their route: tiers apply only when `credentials_cfg is None`.

Tests (real `delegate_task` with `_run_single_child` patched, `_load_config` patched): two tasks easy/hard → children built with each tier's model/provider; task without tier → base; tier `huge` → error and no child built; task `reasoning_effort: low` → child reasoning_config effort low; tier effort used when task has none; `credentials_cfg` callers ignore tiers; schema enum contract (tier values == `DELEGATION_TIERS`).

### Task 3: Session delegation override (backend)

**Files:**
- Modify: `tools/delegate_tool.py` — read `getattr(parent_agent, "_delegation_override", None)`; when it names a model or provider, it becomes the routing cfg for every task (fields `provider`, `model`, `reasoning_effort`) and task tier/effort are ignored.
- Modify: `tui_gateway/methods_config_set.py` — `config.set` key `delegation` (session-scoped, in `_SESSION_SCOPED_KEYS`): value `{provider, model, reasoning_effort}` or `""`/`"auto"` to clear; stores `session["delegation_override"]`, applies to live agent, persists, emits session.info.
- Modify: `tui_gateway/server.py` — `_attach_built_agent` copies `session["delegation_override"]` onto the agent; `_persist_live_session_runtime` writes `model_config["delegation_override"]`; `_session_info` reports `delegation_override` (`{}` when unset).
- Modify: `tui_gateway/methods_session.py` — both resume paths restore `delegation_override` from `model_config`.
- Test: `tests/tools/test_delegate_tiers.py`, `tests/tui_gateway/test_delegation_override.py` (new)

Tests: override beats task tier and task effort; empty override = no effect; config.set stores/clears and validates effort (bad effort → 4002); persisted model_config round-trips through resume record; session.info carries it.

### Task 4: Desktop composer pill "Subagents"

**Files:**
- Create: `apps/desktop/src/app/chat/composer/delegation-pill.tsx` (+ test)
- Modify: `apps/desktop/src/app/chat/composer/controls.tsx` (render next to `ReasoningPill`, hidden in compact mode)
- Modify: session view store for `delegation_override` from session.info (`app/chat/session-view.tsx` / gateway-event `session-info.ts`)
- Modify: `apps/desktop/src/i18n/*.ts`

Behaviour: label `Subagents: Auto` or `Subagents: <model> · <effort>`; menu = `ModelCatalogMenu` with a detached controller (pattern: `plugins/kanban/model-override.tsx`) plus an "Auto (tiers from settings)" row; select/effort edit → `config.set {key:'delegation', session_id, value}`; Auto → value `'auto'`. Optimistic with rollback on error.

Tests (vitest): label for unset/set; select writes config.set with session id; Auto clears; failed write rolls back.

### Task 5: Settings section "Subagent models"

**Files:**
- Modify: `apps/desktop/src/app/settings/model-settings.tsx` (new section after Auxiliary) or a sibling component `delegation-tiers-settings.tsx` (+ test)
- Modify: `apps/desktop/src/app/settings/constants.ts` (drop `delegation.model/provider/reasoning_effort` from the raw advanced list only if the new section fully replaces them — keep otherwise)
- Modify: `apps/desktop/src/i18n/*.ts`

Behaviour: three rows Easy / Normal / Hard, each a model+effort picker (same detached-controller menu) with clear; Normal writes `delegation.provider/model/reasoning_effort`, Easy/Hard write `delegation.tiers.<tier>.provider/model/reasoning_effort`; sparse `saveHermesConfig` patch, clear writes `""` values. Short hint text per row ("Easy — search, mechanical edits", "Hard — review, design").

Tests: each row writes the right keys; clear writes blanks; loaded config renders labels.

### Task 6: Docs + agent guidance

**Files:** `website/docs/user-guide/features/delegation.md`, `website/docs/user-guide/configuration.md`, `tools/AGENTS.md` (Delegation section), `skills/autonomous-ai-agents/hermes-agent/SKILL.md` delegation bullet.

### Task 7: Live verification

- Full delegate suites: `scripts/run_tests.sh tests/tools/test_delegate*.py tests/tools/test_delegate_tiers.py tests/tui_gateway/test_delegation_override.py`; desktop `npx vitest run` on touched files; `npm run typecheck` in apps/desktop.
- Mutation check by hand on the precedence line (break → test fails → restore).
- Real Desktop on this worktree's backend: configure easy/hard to two different models, one parent turn spawning an easy + hard task; confirm `model=`/`provider=` per child in `~/.hermes/logs/agent.log` and on the subagent cards; set composer override → both children on the forced model; Auto → tiers again.
- Adversarial review of the diff (`hermes-adv-review`), fix to dry.
