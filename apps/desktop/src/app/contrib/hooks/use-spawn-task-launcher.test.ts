import { renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { $sessionTiles } from '@/store/session-states'
import { launchSpawnTask, resetSpawnTaskStoreForTests, setSpawnTaskLauncher, type SpawnTaskChoice } from '@/store/spawn-task'

import { spawnTaskWorktreeName, useSpawnTaskLauncher } from './use-spawn-task-launcher'

const isGitRepoPath = vi.fn(async () => true)
const startWorkInRepo = vi.fn(async () => ({ branch: 'hermes/x', path: '/repo/.worktrees/x' }))
const revParse = vi.fn(async () => 'abc123')

vi.mock('@/store/coding-status', () => ({ isGitRepoPath: (p: string) => isGitRepoPath(p) }))
vi.mock('@/store/projects', () => ({
  startWorkInRepo: (repo: string, options: unknown) => startWorkInRepo(repo, options)
}))
vi.mock('@/lib/desktop-git', () => ({ desktopGit: () => ({ review: { revParse } }) }))

const CHOICE: SpawnTaskChoice = { effort: '', fast: false, mode: 'tab', model: '', pin: false, provider: '' }
const OFFER = { cwd: '/repo/.worktrees/feature', ownerStoredSessionId: 'owner', prompt: 'Do it', title: 'Задача', toolCallId: 'c1' }
const ROUTE = { connectionId: 'remote-1', profile: 'work', targetProfile: 'work' }

describe('spawn-task launcher', () => {
  const open = vi.fn(async () => 'stored-new')

  beforeEach(() => {
    window.localStorage.clear()
    resetSpawnTaskStoreForTests()
    open.mockClear()
    startWorkInRepo.mockClear()
    revParse.mockClear()
    renderHook(() => useSpawnTaskLauncher(open))
  })

  afterEach(() => {
    $sessionTiles.set([])
    setSpawnTaskLauncher(null)
  })

  it('starts the session on the offering tile’s backend and profile', async () => {
    $sessionTiles.set([{ ownerRoute: ROUTE, storedSessionId: 'owner' } as never])

    await launchSpawnTask(OFFER, CHOICE, 'scope')

    await waitFor(() => expect(open).toHaveBeenCalledTimes(1))
    expect(open).toHaveBeenCalledWith('center', expect.objectContaining({ cwd: OFFER.cwd, listed: true, route: ROUTE }))
  })

  // The backend runs `worktree add` in the MAIN checkout; without an explicit
  // base a chat living in a feature worktree would branch off main's HEAD.
  it('branches a worktree off the offering checkout’s HEAD, under a fresh name', async () => {
    await launchSpawnTask(OFFER, { ...CHOICE, mode: 'worktree' }, 'scope')

    await waitFor(() => expect(open).toHaveBeenCalledTimes(1))
    expect(revParse).toHaveBeenCalledWith(OFFER.cwd, 'HEAD')
    expect(startWorkInRepo).toHaveBeenCalledWith(OFFER.cwd, expect.objectContaining({ base: 'abc123' }))
    expect(open).toHaveBeenCalledWith('center', expect.objectContaining({ cwd: '/repo/.worktrees/x' }))
  })

  it('never reuses a worktree name, even for titles that slug to nothing', () => {
    expect(spawnTaskWorktreeName('Задача', 1)).not.toBe(spawnTaskWorktreeName('Задача', 2))
    expect(spawnTaskWorktreeName('Задача', 1)).toMatch(/^task-/)
    expect(spawnTaskWorktreeName('Fix Login!', 36)).toBe('fix-login-10')
  })
})
