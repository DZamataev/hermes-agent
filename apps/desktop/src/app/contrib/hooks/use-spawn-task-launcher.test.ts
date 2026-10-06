import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { SessionCreateOverrides } from '@/app/session/hooks/use-session-actions/create-overrides'
import { $sessionTiles } from '@/store/session-states'
import {
  $spawnTaskChips,
  launchSpawnTask,
  resetSpawnTaskStoreForTests,
  setSpawnTaskLauncher,
  spawnTaskChipKey,
  type SpawnTaskChoice,
  takeTileKickoff,
  TILE_KICKOFF_DEADLINE_MS
} from '@/store/spawn-task'

import { spawnTaskWorktreeName, useSpawnTaskLauncher } from './use-spawn-task-launcher'

const isGitRepoPath = vi.fn(async (_path: string) => true)

const startWorkInRepo = vi.fn(async (_repo: string, _options: unknown) => ({
  branch: 'hermes/x',
  path: '/repo/.worktrees/x'
}))

const revParse = vi.fn(async () => 'abc123')
const notify = vi.fn()

vi.mock('@/store/coding-status', () => ({ isGitRepoPath: (p: string) => isGitRepoPath(p) }))
vi.mock('@/store/projects', () => ({
  startWorkInRepo: (repo: string, options: unknown) => startWorkInRepo(repo, options)
}))
vi.mock('@/lib/desktop-git', () => ({ desktopGit: () => ({ review: { revParse } }) }))
vi.mock('@/store/notifications', () => ({ notify: (n: unknown) => notify(n), notifyError: vi.fn() }))

const CHOICE: SpawnTaskChoice = { effort: '', fast: false, mode: 'tab', model: '', pin: false, provider: '' }

const OFFER = {
  cwd: '/repo/.worktrees/feature',
  ownerStoredSessionId: 'owner',
  prompt: '/yolo then fix it',
  title: 'Задача',
  toolCallId: 'c1'
}

const ROUTE = { connectionId: 'remote-1', profile: 'work', targetProfile: 'work' }

type OpenOptions = { createOverrides: SessionCreateOverrides; cwd?: string }

/** Mirrors `openNewSessionTile` + the mounted tile: assign the stored id (which
 *  queues the kickoff), then the tile takes it and settles it as its submit did. */
function tileThatSends(sent: 'never' | boolean) {
  return vi.fn(async (_dir: 'center', options: OpenOptions) => {
    options.createOverrides.onComposerScopeAssigned?.('stored-new')

    if (sent !== 'never') {
      takeTileKickoff('stored-new')?.settle(sent)
    }

    return 'stored-new'
  })
}

describe('spawn-task launcher', () => {
  let open = tileThatSends(true)

  function mountLauncher(next = tileThatSends(true)) {
    open = next
    renderHook(() => useSpawnTaskLauncher(open))
  }

  beforeEach(() => {
    window.localStorage.clear()
    resetSpawnTaskStoreForTests()
    isGitRepoPath.mockClear()
    startWorkInRepo.mockClear()
    revParse.mockClear()
    notify.mockClear()
  })

  afterEach(() => {
    vi.useRealTimers()
    $sessionTiles.set([])
    setSpawnTaskLauncher(null)
  })

  it('starts the session on the offering tile’s backend and profile, and records it launched', async () => {
    mountLauncher()
    $sessionTiles.set([{ ownerRoute: ROUTE, storedSessionId: 'owner' } as never])

    await expect(launchSpawnTask(OFFER, CHOICE, 'scope')).resolves.toBe(true)

    expect(open).toHaveBeenCalledWith('center', expect.objectContaining({ cwd: OFFER.cwd, listed: true, route: ROUTE }))
    expect($spawnTaskChips.get()[spawnTaskChipKey(OFFER)]).toEqual({ state: 'launched', storedSessionId: 'stored-new' })
  })

  // The chip owns the whole selection; "Default model" = the profile default
  // for everything, so not even the fast tier rides along with it.
  it('creates with the chip’s own selection only, deduping its title', async () => {
    mountLauncher()

    await launchSpawnTask(OFFER, CHOICE, 'scope')
    const overrides = open.mock.calls[0]![1].createOverrides

    expect(overrides).toMatchObject({ ownSelection: true, title: OFFER.title, titleDedupe: true })
    expect(overrides).not.toHaveProperty('fast')
    expect(overrides).not.toHaveProperty('model')

    await launchSpawnTask(
      { ...OFFER, toolCallId: 'c2' },
      { ...CHOICE, fast: true, model: 'gpt-5.5', provider: 'openai' },
      'scope'
    )
    expect(open.mock.calls[1]![1].createOverrides).toMatchObject({
      fast: true,
      model: { model: 'gpt-5.5', provider: 'openai' }
    })
  })

  it('hands the tile the task text verbatim, slash and all', async () => {
    let queued: null | string = null

    mountLauncher(
      vi.fn(async (_dir: 'center', options: OpenOptions) => {
        options.createOverrides.onComposerScopeAssigned?.('stored-new')
        const kickoff = takeTileKickoff('stored-new')
        queued = kickoff?.text ?? null
        kickoff?.settle(true)

        return 'stored-new'
      })
    )

    await launchSpawnTask(OFFER, CHOICE, 'scope')

    expect(queued).toBe('/yolo then fix it')
  })

  // A tab that opened but never sent the task is not a launched chip: the
  // user must be told, and the chip must stay launchable.
  it('leaves the chip launchable when the tile refuses the first prompt', async () => {
    mountLauncher(tileThatSends(false))

    await expect(launchSpawnTask(OFFER, CHOICE, 'scope')).resolves.toBe(false)

    expect($spawnTaskChips.get()[spawnTaskChipKey(OFFER)]).toBeUndefined()
    expect(notify).toHaveBeenCalledWith(expect.objectContaining({ kind: 'error' }))
  })

  it('gives up on a tile that never mounts, and withdraws the queued prompt', async () => {
    vi.useFakeTimers()
    mountLauncher(tileThatSends('never'))

    const launched = launchSpawnTask(OFFER, CHOICE, 'scope')
    await vi.advanceTimersByTimeAsync(TILE_KICKOFF_DEADLINE_MS + 1)

    await expect(launched).resolves.toBe(false)
    // A late mount must not send a task the chip already reported as failed.
    expect(takeTileKickoff('stored-new')).toBeNull()
  })

  // A launched chip stays launched: a later click (another window, a stale
  // render) must not open the task a second time.
  it('never launches a chip that already launched', async () => {
    mountLauncher()

    await expect(launchSpawnTask(OFFER, CHOICE, 'scope')).resolves.toBe(true)
    await expect(launchSpawnTask(OFFER, CHOICE, 'scope')).resolves.toBe(false)

    expect(open).toHaveBeenCalledTimes(1)
  })

  // The backend runs `worktree add` in the MAIN checkout; without an explicit
  // base a chat living in a feature worktree would branch off main's HEAD.
  it('branches a worktree off the offering checkout’s HEAD, under a fresh name', async () => {
    mountLauncher()

    await launchSpawnTask(OFFER, { ...CHOICE, mode: 'worktree' }, 'scope')

    await waitFor(() => expect(open).toHaveBeenCalledTimes(1))
    expect(revParse).toHaveBeenCalledWith(OFFER.cwd, 'HEAD')
    expect(startWorkInRepo).toHaveBeenCalledWith(OFFER.cwd, expect.objectContaining({ base: 'abc123' }))
    expect(open).toHaveBeenCalledWith('center', expect.objectContaining({ cwd: '/repo/.worktrees/x' }))
  })

  // Git runs on the window's connection; a chat routed elsewhere has its
  // checkout on another machine.
  it('refuses a worktree for a chat on another backend, touching no git', async () => {
    mountLauncher()
    $sessionTiles.set([{ ownerRoute: ROUTE, storedSessionId: 'owner' } as never])

    await expect(launchSpawnTask(OFFER, { ...CHOICE, mode: 'worktree' }, 'scope')).resolves.toBe(false)

    expect(isGitRepoPath).not.toHaveBeenCalled()
    expect(startWorkInRepo).not.toHaveBeenCalled()
    expect(open).not.toHaveBeenCalled()
  })

  it('never reuses a worktree name, even for titles that slug to nothing', () => {
    expect(spawnTaskWorktreeName('Задача', 1)).not.toBe(spawnTaskWorktreeName('Задача', 2))
    expect(spawnTaskWorktreeName('Задача', 1)).toMatch(/^task-/)
    expect(spawnTaskWorktreeName('Fix Login!', 36)).toBe('fix-login-10')
  })
})
