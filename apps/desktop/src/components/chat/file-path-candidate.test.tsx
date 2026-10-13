import { cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type { HermesConnection } from '@/global'
import { $connection, $currentCwd } from '@/store/session'

import { FilePathCandidate, RESOLVED_PATH_ATTR } from './file-path-candidate'

type Listing = { entries: { isDirectory: boolean; name: string; path: string }[]; error?: string }

const { fs } = vi.hoisted(() => ({
  fs: { listed: [] as string[], readDir: (_dir: string): Promise<Listing> => Promise.resolve({ entries: [] }) }
}))

vi.mock('@/lib/desktop-fs', () => ({
  desktopFsCacheKey: (connection: HermesConnection | null) => `connection:${connection?.connectionId ?? ''}`,
  desktopGitRoot: () => Promise.resolve(null),
  isDesktopFsRemoteMode: () => false,
  readDesktopDir: (dir: string) => {
    fs.listed.push(dir)

    return fs.readDir(dir)
  }
}))

const found = (dir: string, name: string): Listing => ({
  entries: [{ isDirectory: false, name, path: `${dir}/${name}` }]
})

function mount(path: string) {
  const { container } = render(<FilePathCandidate path={path}>{path}</FilePathCandidate>)

  return container.querySelector('[data-file-path]') as HTMLElement
}

/** Rest on the token long enough to probe, and wait for the listing to be asked. */
async function hover(token: HTMLElement) {
  const asked = fs.listed.length

  fireEvent.mouseLeave(token)
  fireEvent.mouseEnter(token)
  await waitFor(() => expect(fs.listed.length).toBeGreaterThan(asked))
}

beforeEach(() => {
  $currentCwd.set('/work/looky')
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  fs.listed.length = 0
  fs.readDir = () => Promise.resolve({ entries: [] })
  $connection.set(null)
  $currentCwd.set('')
})

it('finds a file that was written after the token was first hovered', async () => {
  // The agent names a file and THEN writes it. A miss remembered forever
  // would leave that reference dead for the rest of the session.
  const token = mount('notes/later.md')

  await hover(token)
  expect(token.hasAttribute(RESOLVED_PATH_ATTR)).toBe(false)

  fs.readDir = dir => Promise.resolve(found(dir, 'later.md'))

  // Within the window the miss is still believed: the disk is not asked.
  const asked = fs.listed.length

  fireEvent.mouseLeave(token)
  fireEvent.mouseEnter(token)
  await new Promise(resolve => setTimeout(resolve, 300))
  expect(fs.listed.length).toBe(asked)

  const now = Date.now()

  vi.spyOn(Date, 'now').mockReturnValue(now + 10_000)
  await hover(token)
  await waitFor(() => expect(token.getAttribute(RESOLVED_PATH_ATTR)).toBe('/work/looky/notes/later.md'))
})

it('does not remember a miss when the directory could not be read', async () => {
  // One failed listing says nothing about the file; the very next hover has
  // to ask again rather than wait out a remembered "not there".
  fs.readDir = () => Promise.resolve({ entries: [], error: 'EIO' })

  const token = mount('notes/flaky.md')

  await hover(token)
  expect(token.hasAttribute(RESOLVED_PATH_ATTR)).toBe(false)

  fs.readDir = dir => Promise.resolve(found(dir, 'flaky.md'))
  await hover(token)
  await waitFor(() => expect(token.getAttribute(RESOLVED_PATH_ATTR)).toBe('/work/looky/notes/flaky.md'))
})

it('does not carry an answer from one backend to another', async () => {
  // Same cwd string, same path, different machine: the local hit must not
  // mark a file on the gateway as proven.
  fs.readDir = dir => Promise.resolve(found(dir, 'here.md'))

  const token = mount('notes/here.md')

  await hover(token)
  await waitFor(() => expect(token.hasAttribute(RESOLVED_PATH_ATTR)).toBe(true))

  fs.readDir = () => Promise.resolve({ entries: [] })
  $connection.set({ connectionId: 'gateway' } as HermesConnection)

  await waitFor(() => expect(token.hasAttribute(RESOLVED_PATH_ATTR)).toBe(false))
  await hover(token)
  expect(token.hasAttribute(RESOLVED_PATH_ATTR)).toBe(false)
})
