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
  return `${connectionId ?? ''}::${profile || 'default'}`
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
/** Chip ids with a launch in flight; renderer-only, never persisted. */
export const $spawnTaskLaunching = atom<ReadonlySet<string>>(new Set())

// First prompt for a tile the chip just opened, keyed by its stored id. The
// tile consumes it once on mount and sends it through its own submit pipeline,
// so the task shows up as an ordinary user turn (optimistic bubble included).
const tileKickoffs = new Map<string, string>()

export function queueTileKickoff(storedSessionId: string, text: string): void {
  tileKickoffs.set(storedSessionId, text)
}

export function takeTileKickoff(storedSessionId: string): null | string {
  const text = tileKickoffs.get(storedSessionId) ?? null

  tileKickoffs.delete(storedSessionId)

  return text
}

let launcher: null | SpawnTaskLauncher = null

/** Reactive: a chip mounted before the controller registers must light up when it does. */
export const $spawnTaskLauncherReady = atom(false)

export function setSpawnTaskLauncher(next: null | SpawnTaskLauncher): void {
  launcher = next
  $spawnTaskLauncherReady.set(next !== null)
}

export function setSpawnTaskChoice(scope: string, choice: SpawnTaskChoice): void {
  const next = { ...$spawnTaskChoices.get(), [scope]: choice }

  $spawnTaskChoices.set(next)
  writeJson(CHOICE_KEY, next)
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

function recordChip(key: string, state: SpawnTaskChipState): void {
  const entries = Object.entries({ ...$spawnTaskChips.get(), [key]: state })
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

  setSpawnTaskChoice(scope, choice)
  setLaunching(key, true)

  try {
    const launched = await launcher(offer, choice)

    if (!launched) {
      return false
    }

    recordChip(key, { state: 'launched', storedSessionId: launched.storedSessionId })

    if (choice.pin) {
      pinSession(launched.storedSessionId)
    }

    return true
  } finally {
    setLaunching(key, false)
  }
}

/** Re-read persisted state, as a fresh window would. Tests only. */
export function resetSpawnTaskStoreForTests(): void {
  $spawnTaskChoices.set(loadChoices())
  $spawnTaskChips.set(loadChips())
  $spawnTaskLaunching.set(new Set())
}
