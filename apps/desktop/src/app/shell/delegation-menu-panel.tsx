import { useStore } from '@nanostores/react'
import { useState } from 'react'

import { useSessionView } from '@/app/chat/session-view'
import { detachedModelController, EMPTY_ROUTE, type ModelRoute } from '@/app/shell/detached-model-controller'
import { ModelCatalogMenu } from '@/app/shell/model-catalog-menu'
import { Codicon } from '@/components/ui/codicon'
import { DropdownMenuItem, dropdownMenuRow } from '@/components/ui/dropdown-menu'
import type { HermesGateway } from '@/hermes'
import { useI18n } from '@/i18n'
import { cn } from '@/lib/utils'
import { $draftDelegationOverride, routeToWire, setDraftDelegationOverride } from '@/store/delegation-override'
import { notifyError } from '@/store/notifications'
import { $sessionStates, publishSessionState } from '@/store/session-states'

type GatewayRequest = <T>(method: string, params?: Record<string, unknown>) => Promise<T>

export interface DelegationMenuPanelProps {
  gateway?: HermesGateway
  ownerConnectionId?: string
  profile?: string
  requestGateway?: GatewayRequest
}

/** Paint a session's pick into its runtime slice before the gateway echo (`session.info`) lands. */
function paintSessionPick(runtimeId: string, route: ModelRoute): void {
  const state = $sessionStates.get()[runtimeId]

  if (state) {
    publishSessionState(runtimeId, { ...state, delegationOverride: route })
  }
}

/**
 * The composer's Subagents menu: Auto (each `delegate_task` child picks its
 * model by its task's difficulty tier, mapped in Settings) or one forced
 * model + effort for every child of THIS session. Session-scoped only — the
 * pick never touches config.yaml. A draft holds it locally and ships it on
 * `session.create`; a live session writes it through `config.set
 * key=delegation`, optimistic with rollback.
 */
export function DelegationMenuPanel({ gateway, ownerConnectionId, profile, requestGateway }: DelegationMenuPanelProps) {
  const { t } = useI18n()
  const copy = t.shell.delegation
  const view = useSessionView()
  const runtimeId = useStore(view.$runtimeId)
  const storedId = useStore(view.$storedId)
  const value = useStore(view.$delegationOverride ?? $draftDelegationOverride)
  const [saving, setSaving] = useState(false)
  const request: GatewayRequest | null = requestGateway ?? (gateway ? gateway.request.bind(gateway) : null)
  // Only a true new-chat draft (the primary pane with nothing selected) owns the draft pick. A stored session
  // whose runtime is still resuming — or a tile — has no runtime to write to yet and must never leak a pick
  // into the next new chat's draft.
  const isDraft = view.kind === 'primary' && !storedId

  const commit = async (next: ModelRoute) => {
    if (!runtimeId) {
      if (isDraft) {
        setDraftDelegationOverride(next)
      }

      return
    }

    if (!request || saving) {
      return
    }

    const previous = value
    paintSessionPick(runtimeId, next)
    setSaving(true)

    try {
      await request('config.set', { key: 'delegation', session_id: runtimeId, value: routeToWire(next) })
    } catch (error) {
      paintSessionPick(runtimeId, previous)
      notifyError(error, copy.updateFailed)
    } finally {
      setSaving(false)
    }
  }

  return (
    <ModelCatalogMenu
      controller={detachedModelController(value, next => void commit(next))}
      footer={
        <DropdownMenuItem
          className={cn(dropdownMenuRow, !value.model && 'text-foreground', value.model && 'text-(--ui-text-tertiary)')}
          data-testid="delegation-auto"
          disabled={saving || (!runtimeId && !isDraft)}
          onSelect={() => void commit(EMPTY_ROUTE)}
        >
          <Codicon name={value.model ? 'discard' : 'check'} size="0.75rem" />
          {copy.auto}
        </DropdownMenuItem>
      }
      gateway={gateway}
      ownerConnectionId={ownerConnectionId}
      profile={profile}
      request={requestGateway}
    />
  )
}
