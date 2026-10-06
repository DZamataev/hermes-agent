import { atom } from 'nanostores'

import { readJson, writeJson } from '@/lib/storage'

import { pinSession } from './layout'

/**
 * Side-task chips (`spawn_task` tool calls). The agent only OFFERS a task; the
 * user picks a model + effort on the chip and decides how to run it. Two
 * pieces of persisted renderer state:
 *
 * - the last choice PER BACKEND SCOPE (connection + profile), so the next chip
 *   in the same profile opens on the model/effort/mode picked last time — a
 *   model pair from one profile is never offered to another that may not have
 *   that provider configured;
 * - each chip's outcome keyed by the offering conversation's lineage root +
 *   tool-call id, so a launched or dismissed chip stays that way across
 *   re-renders, reloads and compression (which rotates the session id).
 *
 * The launch itself is the controller's job (session create + first prompt),
 * so the chip calls a launcher the controller registers; `$spawnTaskLauncherReady`
 * tells the chip whether one is.
 */

export type SpawnTaskMode = 'tab' | 'worktree'

export interface SpawnTaskChoice {
  /** '' = the model's default effort, 'none' = thinking off. */
  effort: string
  fast: boolean
  mode: SpawnTaskMode
  /** '' = the profile's default model. */
  model: string
  /** Pin the new session in the sidebar. Rides the sidebar's own pin store,
   *  whose sync writes the same `sessions.pinned` flag `hermes sessions pin` does. */
  pin: boolean
  provider: string
}

export interface SpawnTaskOffer {
  /** Workspace the offering session runs in; a worktree branches off it. */
  cwd: string
  /** The offering conversation's stable identity (lineage root), keys the chip. */
  lineageId?: null | string
  /** The offering session: the spawned one runs on the same backend + profile. */
  ownerStoredSessionId?: null | string
  prompt: string
  title: string
  toolCallId: string
}

export type SpawnTaskChipState = { state: 'dismissed' } | { state: 'launched'; storedSessionId: string }

export type SpawnTaskLauncher = (
  offer: SpawnTaskOffer,
  choice: SpawnTaskChoice
) => Promise<null | { storedSessionId: string }>

const CHOICE_KEY = 'hermes.desktop.spawn-task.choices'
const CHIPS_KEY = 'hermes.desktop.spawn-task.chips'
// Enough to cover every chip still on screen in any realistic transcript set;
// the oldest outcomes fall off so the record cannot grow without bound.
const MAX_CHIPS = 200

export const DEFAULT_SPAWN_TASK_CHOICE: SpawnTaskChoice = {
  effort: '',
  fast: false,
  mode: 'tab',
  model: '',
  pin: false,
  provider: ''
}

function parseChoice(raw: unknown): SpawnTaskChoice {
  if (!raw || typeof raw !== 'object') {
    return DEFAULT_SPAWN_TASK_CHOICE
  }

  const value = raw as Partial<Record<keyof SpawnTaskChoice, unknown>>
  const text = (field: unknown) => (typeof field === 'string' ? field : '')

  return {
    effort: text(value.effort),
    fast: value.fast === true,
    mode: value.mode === 'worktree' ? 'worktree' : 'tab',
    model: text(value.model),
    pin: value.pin === true,
    provider: text(value.provider)
  }
}

function loadChoices(): Record<string, SpawnTaskChoice> {
  const raw = readJson<Record<string, unknown>>(CHOICE_KEY)

  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return {}
  }

  return Object.fromEntries(Object.entries(raw).map(([scope, choice]) => [scope, parseChoice(choice)]))
}

/** The backend scope a choice belongs to: the same pair the model catalog is
 *  keyed by, so a remembered model always comes from the catalog it is offered in. */
export function spawnTaskChoiceScope(connectionId: null | string | undefined, profile: string): string {
  return `${connectionId?.trim() || 'local'}::${profile || 'default'}`
}

function splitChoiceScope(scope: string): { connection: string; profile: string } | null {
  const split = scope.lastIndexOf('::')

  return split < 0 ? null : { connection: scope.slice(0, split), profile: scope.slice(split + 2) }
}

/** A scope on THIS machine's backend: the only one a local profile rename or
 *  delete speaks for (a same-named profile on a remote is a different one). */
function isLocalChoiceScope(scope: string): { connection: string; profile: string } | null {
  const parts = splitChoiceScope(scope)

  return parts && (parts.connection === '' || parts.connection === 'local') ? parts : null
}

function rewriteChoices(next: Record<string, SpawnTaskChoice>): void {
  $spawnTaskChoices.set(next)
  writeJson(CHOICE_KEY, next)
}

/** Profile rename: the remembered choice moves with the profile. */
export function migrateSpawnTaskChoicesForProfile(from: string, to: string): void {
  const current = $spawnTaskChoices.get()
  let changed = false
  const next: Record<string, SpawnTaskChoice> = {}

  for (const [scope, choice] of Object.entries(current)) {
    const local = isLocalChoiceScope(scope)

    if (local?.profile === from) {
      next[`${scope.slice(0, scope.lastIndexOf('::'))}::${to}`] = choice
      changed = true
    } else {
      next[scope] ??= choice
    }
  }

  if (changed) {
    rewriteChoices(next)
  }
}

/** Profile delete: forget its remembered choice — on the named connection,
 *  or this machine's backend when none is given. */
export function dropSpawnTaskChoicesForProfile(profile: string, connectionId?: string): void {
  const current = $spawnTaskChoices.get()
  const connection = connectionId?.trim()

  const removed = (scope: string) => {
    const parts = connection ? splitChoiceScope(scope) : isLocalChoiceScope(scope)

    return parts?.profile === profile && (!connection || parts.connection === connection)
  }

  const next = Object.fromEntries(Object.entries(current).filter(([scope]) => !removed(scope)))

  if (Object.keys(next).length !== Object.keys(current).length) {
    rewriteChoices(next)
  }
}

export function spawnTaskChoiceFor(scope: string): SpawnTaskChoice {
  return $spawnTaskChoices.get()[scope] ?? DEFAULT_SPAWN_TASK_CHOICE
}

function loadChips(): Record<string, SpawnTaskChipState> {
  const raw = readJson<Record<string, SpawnTaskChipState>>(CHIPS_KEY)

  return raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}
}

/** Remembered choices, one per backend scope (`connection::profile`). */
export const $spawnTaskChoices = atom<Record<string, SpawnTaskChoice>>(loadChoices())
export const $spawnTaskChips = atom<Record<string, SpawnTaskChipState>>(loadChips())

// Keep every window's view of chip outcomes and remembered choices live.
if (typeof window !== 'undefined') {
  window.addEventListener('storage', event => {
    if (event.key === CHIPS_KEY) {
      $spawnTaskChips.set(loadChips())
    } else if (event.key === CHOICE_KEY) {
      $spawnTaskChoices.set(loadChoices())
    }
  })
}

/** Chip ids with a launch in flight; renderer-only, never persisted. */
export const $spawnTaskLaunching = atom<ReadonlySet<string>>(new Set())

// First prompt for a tile the chip just opened, keyed by its stored id. The
// tile consumes it once on mount, sends it, and SETTLES it with whether the
// prompt really went out — the chip is "launched" only then. An unsettled
// kickoff (the tile never mounted: an auxiliary window, a bot workspace) is
// withdrawn after a deadline so a late mount cannot send a task the chip
// already reported as failed.
/** How the first prompt fared: `unknown` = it left, but the reply was lost
 *  (a socket drop mid-request) — the turn may be running on the backend. */
export type KickoffOutcome = 'not-sent' | 'sent' | 'unknown'

interface TileKickoff {
  settle: (outcome: KickoffOutcome) => void
  text: string
}

const tileKickoffs = new Map<string, TileKickoff>()

/** How long a launched chip waits for its tile to send the first prompt. */
export const TILE_KICKOFF_DEADLINE_MS = 20_000

export interface QueuedKickoff {
  /** Settles once the tile tried the prompt; `not-sent` on refusal or deadline. */
  sent: Promise<KickoffOutcome>
}

export function queueTileKickoff(storedSessionId: string, text: string): QueuedKickoff {
  let settle: (outcome: KickoffOutcome) => void = () => undefined

  const sent = new Promise<KickoffOutcome>(resolve => {
    settle = resolve
  })

  tileKickoffs.set(storedSessionId, { settle, text })

  const timer = setTimeout(() => {
    if (tileKickoffs.get(storedSessionId)?.text === text) {
      tileKickoffs.delete(storedSessionId)
      settle('not-sent')
    }
  }, TILE_KICKOFF_DEADLINE_MS)

  void sent.then(() => clearTimeout(timer))

  return { sent }
}

export function takeTileKickoff(storedSessionId: string): null | TileKickoff {
  const kickoff = tileKickoffs.get(storedSessionId) ?? null

  tileKickoffs.delete(storedSessionId)

  return kickoff
}

let launcher: null | SpawnTaskLauncher = null

/** Reactive: a chip mounted before the controller registers must light up when it does. */
export const $spawnTaskLauncherReady = atom(false)

export function setSpawnTaskLauncher(next: null | SpawnTaskLauncher): void {
  launcher = next
  $spawnTaskLauncherReady.set(next !== null)
}

export function setSpawnTaskChoice(scope: string, choice: SpawnTaskChoice): void {
  rewriteChoices({ ...$spawnTaskChoices.get(), [scope]: choice })
}

/** A chip's identity: tool-call ids are unique per response, not per app —
 *  some providers number them per reply (`functions.spawn_task:0`), so two
 *  conversations can hold chips with the same id. Scope it by the offering
 *  conversation's lineage root, which survives compression (the live stored
 *  id rotates on compression and would re-arm every chip in the transcript). */
export function spawnTaskChipKey(
  offer: Pick<SpawnTaskOffer, 'lineageId' | 'ownerStoredSessionId' | 'toolCallId'>
): string {
  return `${offer.lineageId || offer.ownerStoredSessionId || ''}::${offer.toolCallId}`
}

/** Another window may have recorded chips since this one loaded: merge onto
 *  what is stored NOW, so a write here never erases a launch made there. */
function recordChip(key: string, state: SpawnTaskChipState): void {
  const entries = Object.entries({ ...$spawnTaskChips.get(), ...loadChips(), [key]: state })
  const next = Object.fromEntries(entries.slice(-MAX_CHIPS))

  $spawnTaskChips.set(next)
  writeJson(CHIPS_KEY, next)
}

export function dismissSpawnTask(key: string): void {
  recordChip(key, { state: 'dismissed' })
}

function setLaunching(key: string, on: boolean): void {
  const next = new Set($spawnTaskLaunching.get())

  if (on) {
    next.add(key)
  } else {
    next.delete(key)
  }

  $spawnTaskLaunching.set(next)
}

/** Launch one chip with `choice`. The choice is remembered either way (the
 *  user picked it); the chip is marked launched only when a session exists.
 *  Returns false when nothing was started — the chip stays launchable. */
export async function launchSpawnTask(
  offer: SpawnTaskOffer,
  choice: SpawnTaskChoice,
  scope: string
): Promise<boolean> {
  const key = spawnTaskChipKey(offer)

  if (!launcher || $spawnTaskLaunching.get().has(key) || $spawnTaskChips.get()[key]) {
    return false
  }

  setLaunching(key, true)

  try {
    return await withChipLaunchClaim(key, async () => {
      // Re-read storage under the claim: the same transcript may be open in
      // another window that launched (or dismissed) this chip meanwhile.
      if (loadChips()[key] || !launcher) {
        return false
      }

      setSpawnTaskChoice(scope, choice)
      const launched = await launcher(offer, choice)

      if (!launched) {
        return false
      }

      recordChip(key, { state: 'launched', storedSessionId: launched.storedSessionId })

      if (choice.pin) {
        pinSession(launched.storedSessionId)
      }

      return true
    })
  } finally {
    setLaunching(key, false)
  }
}

/** One launch of a chip at a time ACROSS windows: a launch takes seconds
 *  (session create + first prompt), and a click in a second window during
 *  that span would otherwise start the task twice. Web Locks are arbitrated
 *  by the browser and freed if the holder closes; the waiter then finds the
 *  holder's outcome in storage. Without Web Locks there is no other window. */
function withChipLaunchClaim<T>(key: string, task: () => Promise<T>): Promise<T> {
  const locks = typeof navigator === 'undefined' ? undefined : navigator.locks

  return locks ? locks.request(`${CHIPS_KEY}.launch.${key}`, task) : task()
}

/** Re-read persisted state, as a fresh window would. Tests only. */
export function resetSpawnTaskStoreForTests(): void {
  $spawnTaskChoices.set(loadChoices())
  $spawnTaskChips.set(loadChips())
  $spawnTaskLaunching.set(new Set())
}
