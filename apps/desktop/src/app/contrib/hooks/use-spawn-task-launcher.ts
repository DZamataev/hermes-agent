import { useEffect, useRef } from 'react'

import type { SessionCreateOverrides } from '@/app/session/hooks/use-session-actions/create-overrides'
import { translateNow } from '@/i18n'
import { desktopGit } from '@/lib/desktop-git'
import { isGitRepoPath } from '@/store/coding-status'
import { notify, notifyError } from '@/store/notifications'
import type { AgentProfileRoute } from '@/store/profile'
import { removeWorktreePath, startWorkInRepo } from '@/store/projects'
import { $connection, knownSessionOwner, ownerLookupSessionRows, setSessions } from '@/store/session'
import { isSessionOwnerRoute } from '@/store/session-request-router'
import { discardSessionTile, sessionTileOwnerRoute } from '@/store/session-states'
import {
  type KickoffOutcome,
  type QueuedKickoff,
  queueTileKickoff,
  setSpawnTaskLauncher,
  type SpawnTaskChoice,
  type SpawnTaskLauncher,
  type SpawnTaskOffer
} from '@/store/spawn-task'
import { canHostSessionTiles } from '@/store/windows'

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
function ownerFor(offer: SpawnTaskOffer) {
  return offer.ownerStoredSessionId
    ? (sessionTileOwnerRoute(offer.ownerStoredSessionId) ??
        knownSessionOwner(ownerLookupSessionRows(), offer.ownerStoredSessionId))
    : undefined
}

/** Same identity rule as `tileBackendIdentityChanged` (session-tile): an
 *  unqualified local window (no connection id, mode local) IS `local`, so a
 *  `local`-routed chat on this machine is never mistaken for a remote one. */
function ownedByAnotherBackend(ownerConnectionId: string | undefined): boolean {
  const owner = String(ownerConnectionId || '').trim()
  const window = $connection.get()

  if (!owner || !window) {
    return false
  }

  const active = String(window.connectionId || '').trim() || (window.mode === 'local' ? 'local' : '')

  return Boolean(active) && owner !== active
}

function ownerRouteFor(offer: SpawnTaskOffer): AgentProfileRoute | undefined {
  const owner = ownerFor(offer)

  return isSessionOwnerRoute(owner) ? owner : undefined
}

function ownerOptions(offer: SpawnTaskOffer): { profile?: string; route?: AgentProfileRoute } {
  const owner = ownerFor(offer)

  if (isSessionOwnerRoute(owner)) {
    return { route: owner }
  }

  return typeof owner === 'string' && owner ? { profile: owner } : {}
}

/** "Default model" means the profile's default for EVERYTHING — fast tier
 *  included: `fast: false` is not "unset", it forces the normal tier over a
 *  profile configured for priority. Fast rides only with a picked model. */
export function createOverrides(
  offer: SpawnTaskOffer,
  choice: SpawnTaskChoice,
  onKickoff: (kickoff: QueuedKickoff) => void = () => undefined
): SessionCreateOverrides {
  return {
    ...(choice.model ? { fast: choice.fast, model: { model: choice.model, provider: choice.provider } } : {}),
    ...(choice.effort ? { reasoningEffort: choice.effort } : {}),
    onComposerScopeAssigned: stored => onKickoff(queueTileKickoff(stored, offer.prompt)),
    ownSelection: true,
    title: offer.title,
    titleDedupe: true
  }
}

/** A worktree name that can never collide with an earlier one: the title's
 *  ASCII slug (a Cyrillic title slugs to nothing) plus a time-based suffix. A
 *  colliding name would make the backend check out the EXISTING `hermes/<slug>`
 *  branch with its old commits instead of starting the task fresh. */
export function spawnTaskWorktreeName(title: string, now = Date.now()): string {
  const slug = title
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 28)
    .replace(/-+$/g, '')

  return `${slug || 'task'}-${now.toString(36)}`
}

/** Where the session starts: the offering workspace, or a fresh worktree off
 *  it. A worktree needs a real repo — `worktreeAdd` would `git init` a plain
 *  folder, which is never what a side task meant. It branches from the
 *  offering checkout's own HEAD: the backend runs `worktree add` in the MAIN
 *  checkout, whose HEAD is the wrong base when the chat lives in a worktree. */
async function launchCwd(
  offer: SpawnTaskOffer,
  choice: SpawnTaskChoice
): Promise<null | { cwd?: string; worktree?: { path: string; repo: string } }> {
  if (choice.mode !== 'worktree') {
    return offer.cwd ? { cwd: offer.cwd } : {}
  }

  // The git facade acts on the WINDOW's connection; a chat routed to another
  // backend has its checkout on that machine. Refuse rather than probe (or
  // create a worktree in) a same-named path on the wrong filesystem.
  if (ownedByAnotherBackend(ownerRouteFor(offer)?.connectionId)) {
    notify({ kind: 'error', message: translateNow('assistant.spawnTask.worktreeOtherBackend') })

    return null
  }

  if (!offer.cwd || !(await isGitRepoPath(offer.cwd))) {
    notify({ kind: 'error', message: translateNow('assistant.spawnTask.worktreeNeedsRepo') })

    return null
  }

  const base = await desktopGit()?.review?.revParse(offer.cwd, 'HEAD').catch(() => null)

  // Without the offering checkout's HEAD the backend would branch from the
  // MAIN checkout — the wrong base, silently. Refuse instead.
  if (!base) {
    notify({ kind: 'error', message: translateNow('assistant.spawnTask.worktreeNoBase') })

    return null
  }

  const tree = await startWorkInRepo(offer.cwd, { base, name: spawnTaskWorktreeName(offer.title) })

  return tree ? { cwd: tree.path, worktree: { path: tree.path, repo: offer.cwd } } : null
}

/** Undo what a launch left behind when its task provably never reached the
 *  backend, so a retry starts clean: the tab (discarded — ⌘⇧T must not bring
 *  back a tab declared dead), its sidebar row, and the fresh worktree. The
 *  branch stays (`worktree remove` keeps it); names never collide. Best effort.
 *  NOT for an outcome-unknown send (see `KickoffOutcome`): the turn may be
 *  running there, in that worktree. */
async function rollbackLaunch(stored: null | string, worktree?: { path: string; repo: string }): Promise<void> {
  if (stored) {
    discardSessionTile(stored)
    setSessions(prev => prev.filter(session => session.id !== stored))
  }

  if (worktree) {
    await removeWorktreePath(worktree.repo, worktree.path, { force: true }).catch(() => undefined)
  }
}

/** Registers how a spawn-task chip starts its session: open a listed tab on
 *  the chip's model and hand it the task as its first prompt. */
/** `canHostTiles` defaults to this window's kind: tiles mount only where the
 *  pane tree does (see the controller's `watchSessionTiles`). */
export function useSpawnTaskLauncher(
  openNewSessionTile: OpenNewSessionTile,
  canHostTiles: boolean = canHostSessionTiles()
): void {
  const openRef = useRef(openNewSessionTile)
  openRef.current = openNewSessionTile

  useEffect(() => {
    // A window that mounts no tile tree (HUD, popped-out browser) could open
    // the session but never send its task: register nothing, so the chip
    // shows as unavailable instead of failing 20 s after a click.
    if (!canHostTiles) {
      return
    }

    const launch: SpawnTaskLauncher = async (offer, choice) => {
      let start: Awaited<ReturnType<typeof launchCwd>> = null
      let stored: null | string = null

      try {
        start = await launchCwd(offer, choice)

        if (start === null) {
          return null
        }

        let kickoff: null | QueuedKickoff = null

        stored = await openRef.current('center', {
          ...ownerOptions(offer),
          createOverrides: createOverrides(offer, choice, queued => (kickoff = queued)),
          ...(start.cwd ? { cwd: start.cwd } : {}),
          listed: true
        })

        // "Launched" means the task reached the new session, not just that a
        // tab opened: the tile's first submit can be refused or never happen.
        const outcome: KickoffOutcome = stored && kickoff ? await (kickoff as QueuedKickoff).sent : 'not-sent'

        if (outcome === 'unknown') {
          // The prompt left but the reply was lost (socket drop): the turn may
          // be running. Keep everything and count the chip as launched — a
          // retry would run the task twice.
          notify({ kind: 'warning', message: translateNow('assistant.spawnTask.kickoffUnknown') })

          return { storedSessionId: stored! }
        }

        if (outcome !== 'sent') {
          if (stored) {
            notify({ kind: 'error', message: translateNow('assistant.spawnTask.kickoffFailed') })
          }

          await rollbackLaunch(stored, start.worktree)

          return null
        }

        return { storedSessionId: stored! }
      } catch (error) {
        notifyError(error, translateNow('assistant.spawnTask.launchFailed'))
        await rollbackLaunch(stored, start?.worktree)

        return null
      }
    }

    setSpawnTaskLauncher(launch)

    return () => setSpawnTaskLauncher(null)
  }, [canHostTiles])
}
