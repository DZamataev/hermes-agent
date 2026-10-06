/**
 * Per-create overrides, translated to `session.create` params and folded over
 * the ones `desktopSessionCreateParams` derived from the visible selection.
 *
 * Reasoning effort may ride alone: the guided onboarding chat wants `minimal`
 * on whatever model the backend already resolved for the profile. A model pin
 * travels only as a `{model, provider}` PAIR — the composer's model and
 * provider belong together, and overriding one without the other mints a
 * session pointing a provider at a model it does not serve. The spawn-task
 * chip is the caller that pins one, from its own picker.
 */
export interface SessionCreateOverrides {
  fast?: boolean
  model?: { model: string; provider: string }
  /** Renderer-only handoff, fired at the stored-id assignment before navigation. */
  onComposerScopeAssigned?: (scope: string) => void
  /** The caller owns the WHOLE selection: the composer's sticky model / effort /
   *  fast never ride along, so an unset field means the profile's default. */
  ownSelection?: boolean
  reasoningEffort?: string
  title?: string
  /** The title is a proposal (model-written): if another session holds it,
   *  keep it as `<title> #N` instead of dropping it for an auto-title. */
  titleDedupe?: boolean
}

export type CreateBackendSessionForSend = (
  preview?: string | null,
  seedMessages?: SessionSeedMessage[],
  createOverrides?: SessionCreateOverrides
) => Promise<string | null>

export interface SessionSeedMessage {
  content: string
  display_kind?: 'hidden'
  role: 'assistant' | 'user'
}

export interface SessionCreateOverrideParams {
  fast?: boolean
  messages?: SessionSeedMessage[]
  model?: string
  provider?: string
  reasoning_effort?: string
  title?: string
  title_dedupe?: boolean
}

export function sessionCreateOverrideParams(
  overrides: SessionCreateOverrides | undefined,
  seedMessages?: SessionSeedMessage[]
): SessionCreateOverrideParams {
  const params: SessionCreateOverrideParams = {}

  if (overrides?.title) {
    params.title = overrides.title

    if (overrides.titleDedupe) {
      params.title_dedupe = true
    }
  }

  const model = overrides?.model?.model.trim()

  if (model) {
    params.model = model
    params.provider = overrides?.model?.provider.trim() ?? ''
  }

  if (overrides?.reasoningEffort) {
    params.reasoning_effort = overrides.reasoningEffort
  }

  if (overrides?.fast !== undefined) {
    params.fast = overrides.fast
  }

  if (seedMessages?.length) {
    params.messages = seedMessages
  }

  return params
}
