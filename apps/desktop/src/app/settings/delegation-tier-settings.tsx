import { isReasoningEffort } from '@hermes/shared'
import { useState } from 'react'

import { detachedModelController, EMPTY_ROUTE, type ModelRoute } from '@/app/shell/detached-model-controller'
import { ModelCatalogMenu, ModelMenuCloseContext } from '@/app/shell/model-catalog-menu'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import type { HermesConfigRecord } from '@/types/hermes'

import { getNested } from './helpers'
import { ListRow, Pill } from './primitives'

export type DelegationTier = 'easy' | 'hard' | 'normal'

export const DELEGATION_TIERS: readonly DelegationTier[] = ['easy', 'normal', 'hard']

/** Config keys a tier row owns. "normal" IS the base `delegation.*` block (no migration for old configs). */
export function tierKeys(tier: DelegationTier): { effort: string; model: string; provider: string } {
  const base = tier === 'normal' ? 'delegation' : `delegation.tiers.${tier}`

  return { effort: `${base}.reasoning_effort`, model: `${base}.model`, provider: `${base}.provider` }
}

const text = (value: unknown): string => (typeof value === 'string' ? value.trim() : value === false ? 'none' : '')

export function tierRoute(config: HermesConfigRecord, tier: DelegationTier): ModelRoute {
  const keys = tierKeys(tier)

  return {
    effort: text(getNested(config, keys.effort)),
    model: text(getNested(config, keys.model)),
    provider: text(getNested(config, keys.provider))
  }
}

/** The (key, value) writes that set *tier* to *route*; an empty route clears all three ("" = unset, the
 *  same convention the backend's tier merge reads as "not overridden"). */
export function tierWrites(tier: DelegationTier, route: ModelRoute): [string, string][] {
  const keys = tierKeys(tier)

  return [
    [keys.provider, route.model ? route.provider : ''],
    [keys.model, route.model],
    [keys.effort, route.model ? route.effort : '']
  ]
}

function TierPicker({
  label,
  onChange,
  value
}: {
  label: string
  onChange: (next: ModelRoute) => void
  value: ModelRoute
}) {
  const [open, setOpen] = useState(false)

  return (
    <DropdownMenu onOpenChange={setOpen} open={open}>
      <DropdownMenuTrigger asChild>
        <Button
          className="h-8 w-full justify-between gap-2 px-2.5 text-[0.75rem] font-normal @2xl:w-72"
          type="button"
          variant="outline"
        >
          <span className={cn('min-w-0 truncate', !value.model && 'text-(--ui-text-tertiary)')}>{label}</span>
          <Codicon className="opacity-50" name="chevron-down" size="0.7rem" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72 p-0">
        <ModelMenuCloseContext.Provider value={() => setOpen(false)}>
          <ModelCatalogMenu controller={detachedModelController(value, onChange)} />
        </ModelMenuCloseContext.Provider>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/**
 * Settings → Advanced → Subagents: the model each `delegate_task` difficulty
 * tier runs on. The parent model names a tier per task (easy / normal / hard)
 * and never a model id, so this map is the whole routing policy. "Normal" is
 * the existing `delegation.provider/model/reasoning_effort`; easy and hard
 * fall back to it when unset. A chat can still force one model for all its
 * subagents from the composer's Subagents pill — that pick never lands here.
 */
export function DelegationTierSettings({
  config,
  onChange
}: {
  config: HermesConfigRecord
  onChange: (writes: [string, string][]) => void
}) {
  const { t } = useI18n()
  const copy = t.settings.delegationTiers

  const effortLabel = (effort: string) =>
    effort === 'none' ? copy.thinkingOff : isReasoningEffort(effort) ? t.shell.modelOptions[effort] : effort

  return (
    <div className="mb-4 grid gap-1" data-testid="delegation-tier-settings">
      <p className="mb-1 text-xs text-muted-foreground">{copy.description}</p>
      {DELEGATION_TIERS.map(tier => {
        const route = tierRoute(config, tier)
        const fallback = tier === 'normal' ? copy.inheritParent : copy.inheritNormal
        // A configured base_url wins over provider in the backend (direct endpoint), so name the endpoint.
        const endpoint = text(getNested(config, `${tier === 'normal' ? 'delegation' : `delegation.tiers.${tier}`}.base_url`))

        const label = route.model
          ? [
              endpoint ? `${endpoint}: ${route.model}` : route.provider ? `${route.provider}: ${route.model}` : route.model,
              route.effort && effortLabel(route.effort)
            ]
              .filter(Boolean)
              .join(' · ')
          : fallback

        return (
          <div
            className="scroll-mt-6 rounded-lg"
            data-testid={`delegation-tier-${tier}`}
            id={`delegation-tier-${tier}`}
            key={tier}
          >
            <ListRow
              action={
                <div className="flex items-center gap-1">
                  <TierPicker label={label} onChange={next => onChange(tierWrites(tier, next))} value={route} />
                  {route.model ? (
                    <Button
                      aria-label={copy.clear}
                      onClick={() => onChange(tierWrites(tier, EMPTY_ROUTE))}
                      size="icon"
                      type="button"
                      variant="ghost"
                    >
                      <Codicon name="close" size="0.7rem" />
                    </Button>
                  ) : null}
                </div>
              }
              description={copy.hints[tier]}
              title={
                <span className="flex items-baseline gap-2">
                  {copy.tiers[tier]}
                  {tier === 'normal' ? <Pill>{copy.defaultPill}</Pill> : null}
                </span>
              }
            />
          </div>
        )
      })}
    </div>
  )
}
