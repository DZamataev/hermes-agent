import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  $spawnTaskChips,
  $spawnTaskChoice,
  dismissSpawnTask,
  launchSpawnTask,
  resetSpawnTaskStoreForTests,
  setSpawnTaskChoice,
  setSpawnTaskLauncher,
  spawnTaskChipKey,
  type SpawnTaskChoice
} from './spawn-task'

const CHOICE: SpawnTaskChoice = { effort: 'high', fast: false, mode: 'worktree', model: 'gpt-5.5', provider: 'openai' }

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
  })

  afterEach(() => setSpawnTaskLauncher(null))

  it('remembers the last choice across a reload, so the next chip opens on it', () => {
    setSpawnTaskChoice(CHOICE)
    resetSpawnTaskStoreForTests()

    expect($spawnTaskChoice.get()).toEqual(CHOICE)
  })

  it('launches through the registered launcher with the chosen model and records the session', async () => {
    const launcher = vi.fn(async () => ({ storedSessionId: 'stored-9' }))
    setSpawnTaskLauncher(launcher)

    await launchSpawnTask(OFFER, CHOICE)

    expect(launcher).toHaveBeenCalledWith(OFFER, CHOICE)
    expect($spawnTaskChips.get()[KEY]).toEqual({ state: 'launched', storedSessionId: 'stored-9' })
    expect($spawnTaskChoice.get()).toEqual(CHOICE)

    resetSpawnTaskStoreForTests()
    expect($spawnTaskChips.get()[KEY]?.state).toBe('launched')
  })

  it('leaves the chip launchable when the launch fails', async () => {
    setSpawnTaskLauncher(async () => null)

    await expect(launchSpawnTask(OFFER, CHOICE)).resolves.toBe(false)
    expect($spawnTaskChips.get()[KEY]).toBeUndefined()
  })

  it('refuses a second launch of the same chip', async () => {
    const launcher = vi.fn(async () => ({ storedSessionId: 'stored-9' }))
    setSpawnTaskLauncher(launcher)

    await Promise.all([launchSpawnTask(OFFER, CHOICE), launchSpawnTask(OFFER, CHOICE)])

    expect(launcher).toHaveBeenCalledTimes(1)
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

    await launchSpawnTask(OFFER, CHOICE)
    const other = { ...OFFER, ownerStoredSessionId: 'session-b' }

    expect($spawnTaskChips.get()[spawnTaskChipKey(other)]).toBeUndefined()
    await expect(launchSpawnTask(other, CHOICE)).resolves.toBe(true)
    expect(launcher).toHaveBeenCalledTimes(2)
  })
})
