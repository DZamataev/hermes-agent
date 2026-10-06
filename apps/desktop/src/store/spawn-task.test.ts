import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $pinnedSessionIds } from './layout'
import {
  $spawnTaskChips,
  $spawnTaskLauncherReady,
  dismissSpawnTask,
  launchSpawnTask,
  resetSpawnTaskStoreForTests,
  setSpawnTaskChoice,
  setSpawnTaskLauncher,
  spawnTaskChipKey,
  spawnTaskChoiceFor,
  spawnTaskChoiceScope,
  type SpawnTaskChoice
} from './spawn-task'

const SCOPE = spawnTaskChoiceScope('local', 'default')

const CHOICE: SpawnTaskChoice = {
  effort: 'high',
  fast: false,
  mode: 'worktree',
  model: 'gpt-5.5',
  pin: false,
  provider: 'openai'
}

const OFFER = {
  cwd: '/repo',
  ownerStoredSessionId: 'session-a',
  prompt: 'Fix the flaky login test',
  title: 'Flaky login',
  toolCallId: 'call-1'
}

const KEY = spawnTaskChipKey(OFFER)

describe('spawn-task store', () => {
  beforeEach(() => {
    window.localStorage.clear()
    resetSpawnTaskStoreForTests()
    $pinnedSessionIds.set([])
  })

  afterEach(() => setSpawnTaskLauncher(null))

  it('remembers the last choice across a reload, so the next chip opens on it', () => {
    setSpawnTaskChoice(SCOPE, CHOICE)
    resetSpawnTaskStoreForTests()

    expect(spawnTaskChoiceFor(SCOPE)).toEqual(CHOICE)
  })

  // A model pair is only meaningful to the backend+profile whose catalog it
  // came from: another profile may not have that provider configured at all.
  it('keeps the remembered choice per profile and per connection', () => {
    setSpawnTaskChoice(SCOPE, CHOICE)

    expect(spawnTaskChoiceFor(spawnTaskChoiceScope('local', 'work')).model).toBe('')
    expect(spawnTaskChoiceFor(spawnTaskChoiceScope('remote-1', 'default')).model).toBe('')
  })

  it('tells the chip when a launcher appears, so it never stays disabled', () => {
    expect($spawnTaskLauncherReady.get()).toBe(false)
    setSpawnTaskLauncher(async () => null)
    expect($spawnTaskLauncherReady.get()).toBe(true)
    setSpawnTaskLauncher(null)
    expect($spawnTaskLauncherReady.get()).toBe(false)
  })

  // Compression rotates the live stored id; a chip keyed by it would re-arm
  // and could launch the same task twice.
  it('keys a chip by the conversation lineage, so compression does not re-arm it', () => {
    const before = spawnTaskChipKey({ lineageId: 'root', ownerStoredSessionId: 'root', toolCallId: 'call-1' })
    const after = spawnTaskChipKey({ lineageId: 'root', ownerStoredSessionId: 'tip-2', toolCallId: 'call-1' })

    expect(after).toBe(before)
  })

  it('launches through the registered launcher with the chosen model and records the session', async () => {
    const launcher = vi.fn(async () => ({ storedSessionId: 'stored-9' }))
    setSpawnTaskLauncher(launcher)

    await launchSpawnTask(OFFER, CHOICE, SCOPE)

    expect(launcher).toHaveBeenCalledWith(OFFER, CHOICE)
    expect($spawnTaskChips.get()[KEY]).toEqual({ state: 'launched', storedSessionId: 'stored-9' })
    expect(spawnTaskChoiceFor(SCOPE)).toEqual(CHOICE)

    resetSpawnTaskStoreForTests()
    expect($spawnTaskChips.get()[KEY]?.state).toBe('launched')
  })

  it('leaves the chip launchable when the launch fails', async () => {
    setSpawnTaskLauncher(async () => null)

    await expect(launchSpawnTask(OFFER, CHOICE, SCOPE)).resolves.toBe(false)
    expect($spawnTaskChips.get()[KEY]).toBeUndefined()
  })

  it('refuses a second launch of the same chip', async () => {
    const launcher = vi.fn(async () => ({ storedSessionId: 'stored-9' }))
    setSpawnTaskLauncher(launcher)

    await Promise.all([launchSpawnTask(OFFER, CHOICE, SCOPE), launchSpawnTask(OFFER, CHOICE, SCOPE)])

    expect(launcher).toHaveBeenCalledTimes(1)
  })

  it('pins the launched session when the chip asked for it, and only then', async () => {
    setSpawnTaskLauncher(async offer => ({ storedSessionId: `stored-${offer.toolCallId}` }))

    await launchSpawnTask({ ...OFFER, toolCallId: 'pinned' }, { ...CHOICE, pin: true }, SCOPE)
    await launchSpawnTask({ ...OFFER, toolCallId: 'loose' }, CHOICE, SCOPE)

    expect($pinnedSessionIds.get()).toContain('stored-pinned')
    expect($pinnedSessionIds.get()).not.toContain('stored-loose')
    expect(spawnTaskChoiceFor(SCOPE).pin).toBe(false)
  })

  it('dismisses a chip for good', () => {
    dismissSpawnTask(KEY)
    resetSpawnTaskStoreForTests()

    expect($spawnTaskChips.get()[KEY]).toEqual({ state: 'dismissed' })
  })

  // Tool-call ids are unique per response, not per app: some providers number
  // them per reply (`functions.spawn_task:0`), so another session can reuse one.
  it('keeps chips in different sessions apart even when their tool-call ids collide', async () => {
    const launcher = vi.fn(async () => ({ storedSessionId: 'stored-9' }))
    setSpawnTaskLauncher(launcher)

    await launchSpawnTask(OFFER, CHOICE, SCOPE)
    const other = { ...OFFER, ownerStoredSessionId: 'session-b' }

    expect($spawnTaskChips.get()[spawnTaskChipKey(other)]).toBeUndefined()
    await expect(launchSpawnTask(other, CHOICE, SCOPE)).resolves.toBe(true)
    expect(launcher).toHaveBeenCalledTimes(2)
  })
})
