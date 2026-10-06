import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { useSessionView } from '@/app/chat/session-view'
import { ModelMenuCloseContext } from '@/app/shell/model-menu-panel'
import { Button } from '@/components/ui/button'
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { releaseTypingFocus } from '@/components/ui/keyboard-first'
import { Tip } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { ChevronDown } from '@/lib/icons'
import { reasoningEffortLabel } from '@/lib/reasoning-effort'
import { cn } from '@/lib/utils'
import { $draftDelegationOverride } from '@/store/delegation-override'

import type { ChatBarState } from './types'

const PILL = cn(
  'h-(--composer-control-size) min-w-0 shrink gap-1 rounded-md px-2 text-xs font-normal',
  'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
)

/** `model · Effort`, or the bare model when the pick inherits effort. */
export function delegationPickLabel(model: string, effort: string): string {
  const name = model.includes('/') ? model.slice(model.lastIndexOf('/') + 1) : model

  return effort ? `${name} · ${reasoningEffortLabel(effort)}` : name
}

/**
 * Composer "Subagents" selector, next to the model and reasoning pills: which
 * model `delegate_task` children of THIS session run on. Auto = each task's
 * difficulty tier picks from Settings → Subagent models; a pick forces one
 * model + effort for every child of the session. Reads this surface's
 * SessionView, like the model pill.
 */
export function DelegationPill({ disabled, model }: { disabled: boolean; model: ChatBarState['model'] }) {
  const copy = useI18n().t.shell.delegation
  const view = useSessionView()
  const value = useStore(view.$delegationOverride ?? $draftDelegationOverride)
  const [open, setOpen] = useState(false)

  if (!model.delegationMenuContent) {
    return null
  }

  const label = value.model ? delegationPickLabel(value.model, value.effort) : copy.auto
  const title = value.model
    ? copy.forcedTitle(value.provider ? `${value.provider}: ${value.model}` : value.model)
    : copy.autoTitle

  const setMenuOpen = (next: boolean) => {
    setOpen(next)

    if (!next) {
      releaseTypingFocus()
    }
  }

  return (
    <DropdownMenu onOpenChange={setMenuOpen} open={open}>
      <Tip label={title} side="top">
        <DropdownMenuTrigger asChild>
          <Button
            aria-label={title}
            className={cn(PILL, value.model && 'text-(--ui-text-secondary)')}
            data-testid="delegation-pill"
            disabled={disabled}
            type="button"
            variant="ghost"
          >
            <span className="shrink-0 opacity-70">{copy.pillPrefix}</span>
            <span className="min-w-0 truncate">{label}</span>
            <ChevronDown className="size-2.5 shrink-0 opacity-50" />
          </Button>
        </DropdownMenuTrigger>
      </Tip>
      <DropdownMenuContent align="end" className="w-72 p-0" side="top" sideOffset={8}>
        <ModelMenuCloseContext.Provider value={() => setMenuOpen(false)}>
          {model.delegationMenuContent}
        </ModelMenuCloseContext.Provider>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
