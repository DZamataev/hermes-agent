import type { ModelMenuController } from './model-catalog-menu'

/** A provider/model/effort choice held outside any live session ('' = unset / inherit). */
export interface ModelRoute {
  /** '' = inherit, 'none' = thinking off, else a reasoning level. */
  effort: string
  model: string
  provider: string
}

export const EMPTY_ROUTE: ModelRoute = { effort: '', model: '', provider: '' }

export const isEmptyRoute = (route: ModelRoute): boolean => !route.model.trim()

/**
 * A `ModelCatalogMenu` controller that edits a detached value instead of a
 * live session — the subagent pick in the composer and the subagent tier rows
 * in Settings. Presets are read-only here on purpose: a subagent choice must
 * never rewrite what the main composer opens a model at.
 */
export function detachedModelController(value: ModelRoute, onChange: (next: ModelRoute) => void): ModelMenuController {
  return {
    applyPreset: (preset, row) => onChange({ effort: preset.effort ?? '', model: row.model, provider: row.provider }),
    current: { effort: value.effort, fast: false, model: value.model, provider: value.provider },
    presetFor: () => ({}),
    select: (model, provider) => onChange({ ...value, model, provider }),
    // Fast is a live-session request parameter a delegated child does not carry.
    setOptions: (patch, row) => {
      if (patch.effort !== undefined) {
        onChange({ effort: patch.effort, model: row.model, provider: row.provider })
      }
    }
  }
}
