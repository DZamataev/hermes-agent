import { useEffect, useRef } from 'react'

import type { SessionCreateOverrides } from '@/app/session/hooks/use-session-actions/create-overrides'
import { translateNow } from '@/i18n'
import { isGitRepoPath } from '@/store/coding-status'
import { notify, notifyError } from '@/store/notifications'
import type { AgentProfileRoute } from '@/store/profile'
import { startWorkInRepo } from '@/store/projects'
import { knownSessionOwner, ownerLookupSessionRows } from '@/store/session'
import { isSessionOwnerRoute } from '@/store/session-request-router'
import { sessionTileOwnerRoute } from '@/store/session-states'
import {
  queueTileKickoff,
  setSpawnTaskLauncher,
  type SpawnTaskChoice,
  type SpawnTaskLauncher,
  type SpawnTaskOffer
} from '@/store/spawn-task'

type OpenNewSessionTile = (
  dir: 'center',
  options: {
    createOverrides: SessionCreateOverrides
    cwd?: string
    listed: boolean
    profile?: string
    route?: AgentProfileRoute
  }
) => Promise<null | string>

/** The spawned session runs where the offering one does: same connection and
 *  profile. A bare profile name is all an older row may know. */
function ownerOptions(offer: SpawnTaskOffer): { profile?: string; route?: AgentProfileRoute } {
  const owner = offer.ownerStoredSessionId
    ? (sessionTileOwnerRoute(offer.ownerStoredSessionId) ??
      knownSessionOwner(ownerLookupSessionRows(), offer.ownerStoredSessionId))
    : undefined

  if (isSessionOwnerRoute(owner)) {
    return { route: owner }
  }

  return typeof owner === 'string' && owner ? { profile: owner } : {}
}

function createOverrides(offer: SpawnTaskOffer, choice: SpawnTaskChoice): SessionCreateOverrides {
  return {
    fast: choice.fast,
    ...(choice.model ? { model: { model: choice.model, provider: choice.provider } } : {}),
    ...(choice.effort ? { reasoningEffort: choice.effort } : {}),
    onComposerScopeAssigned: stored => queueTileKickoff(stored, offer.prompt),
    title: offer.title
  }
}

/** Where the session starts: the offering workspace, or a fresh worktree off
 *  it. A worktree needs a real repo — `worktreeAdd` would `git init` a plain
 *  folder, which is never what a side task meant. */
async function launchCwd(offer: SpawnTaskOffer, choice: SpawnTaskChoice): Promise<null | string | undefined> {
  if (choice.mode !== 'worktree') {
    return offer.cwd || undefined
  }

  if (!offer.cwd || !(await isGitRepoPath(offer.cwd))) {
    notify({ kind: 'error', message: translateNow('assistant.spawnTask.worktreeNeedsRepo') })

    return null
  }

  const tree = await startWorkInRepo(offer.cwd, { name: offer.title })

  return tree?.path ?? null
}

/** Registers how a spawn-task chip starts its session: open a listed tab on
 *  the chip's model and hand it the task as its first prompt. */
export function useSpawnTaskLauncher(openNewSessionTile: OpenNewSessionTile): void {
  const openRef = useRef(openNewSessionTile)
  openRef.current = openNewSessionTile

  useEffect(() => {
    const launch: SpawnTaskLauncher = async (offer, choice) => {
      try {
        const cwd = await launchCwd(offer, choice)

        if (cwd === null) {
          return null
        }

        const stored = await openRef.current('center', {
          ...ownerOptions(offer),
          createOverrides: createOverrides(offer, choice),
          ...(cwd ? { cwd } : {}),
          listed: true
        })

        return stored ? { storedSessionId: stored } : null
      } catch (error) {
        notifyError(error, translateNow('assistant.spawnTask.launchFailed'))

        return null
      }
    }

    setSpawnTaskLauncher(launch)

    return () => setSpawnTaskLauncher(null)
  }, [])
}
