import { expect, it } from 'vitest'

import { resolveFilePath } from './file-path-resolve'

const TREE: Record<string, string[]> = {
  '/work/looky': ['app', 'look-messenger', 'package.json'],
  '/work/looky/look-messenger/details/header': ['ChatMessagesHeader.tsx'],
  '/work/looky/docs': ['plan.md'],
  '/work/looky/app/features': ['chats']
}

function io(tree: Record<string, string[]> = TREE, gitRootOf: string | null = '/work/looky') {
  const listed: string[] = []

  return {
    gitRoot: () => Promise.resolve(gitRootOf),
    listDir: (dir: string) => {
      listed.push(dir)

      // The bridge reports each entry's own absolute path; for an ordinary
      // directory that is just the listed dir plus the name.
      return Promise.resolve(tree[dir]?.map(name => ({ name, path: `${dir}/${name}` })) ?? null)
    },
    listed
  }
}

it('resolves a home-relative path against the real home directory', async () => {
  // `~/…` is not relative to the session: joining it to the cwd yields
  // `/work/looky/~/…`, which cannot exist, so the reference silently never
  // resolved. The bridge reports each entry's own absolute path, so the
  // listing is what expands the tilde — the renderer never guesses a home.
  const fs = {
    gitRoot: () => Promise.resolve('/work/looky'),
    listDir: (dir: string) =>
      Promise.resolve(dir === '~/dev' ? [{ name: 'notes.md', path: '/Users/me/dev/notes.md' }] : null)
  }

  expect(await resolveFilePath('~/dev/notes.md', '/work/looky', fs)).toEqual({
    base: 'cwd',
    isDirectory: false,
    path: '/Users/me/dev/notes.md'
  })
})

it('reports a directory as one, on the filesystem\'s word rather than the token\'s shape', async () => {
  // A trailing slash is how an author announces a directory, but it is not
  // proof: surfaces that must not offer a preview for a folder need the
  // listing's verdict, and a path written without the slash is a folder just
  // the same.
  const fs = {
    gitRoot: () => Promise.resolve('/work/looky'),
    listDir: (dir: string) =>
      Promise.resolve(
        dir === '/work/looky/app'
          ? [{ isDirectory: true, name: 'features', path: '/work/looky/app/features' }]
          : null
      )
  }

  expect(await resolveFilePath('app/features', '/work/looky', fs)).toEqual({
    base: 'cwd',
    isDirectory: true,
    path: '/work/looky/app/features'
  })
})

it('resolves a path that exists under the session cwd', async () => {
  expect(await resolveFilePath('docs/plan.md', '/work/looky', io())).toEqual({
    base: 'cwd',
    isDirectory: false,
    path: '/work/looky/docs/plan.md'
  })
})

it('resolves a repository-root-relative path from a session inside a subdirectory', async () => {
  // The agent names paths from the repo root; the session sits deeper.
  expect(await resolveFilePath('docs/plan.md', '/work/looky/app/features', io())).toEqual({
    base: 'git-root',
    isDirectory: false,
    path: '/work/looky/docs/plan.md'
  })
})

it('does not resolve a path whose leading segment the author omitted', async () => {
  // The real file is look-messenger/details/header/ChatMessagesHeader.tsx: this
  // names neither an existing cwd-relative nor root-relative file, so the UI
  // must keep it as prose instead of offering a link that cannot open.
  expect(await resolveFilePath('details/header/ChatMessagesHeader.tsx', '/work/looky', io())).toBeNull()
})

it('keeps an absolute path as its own single candidate', async () => {
  const fs = io()

  expect(await resolveFilePath('/work/looky/docs/plan.md', '/elsewhere', fs)).toEqual({
    base: 'cwd',
    isDirectory: false,
    path: '/work/looky/docs/plan.md'
  })
  expect(fs.listed).toEqual(['/work/looky/docs'])
})

it('reads each directory once however many candidates share it', async () => {
  const fs = io(TREE, '/work/looky')

  // cwd IS the git root here, so both candidates would name the same directory.
  await resolveFilePath('missing/file.md', '/work/looky', fs)
  expect(new Set(fs.listed).size).toBe(fs.listed.length)
})

it('treats an unreadable directory as absence rather than failing', async () => {
  const fs = {
    gitRoot: () => Promise.resolve('/work/looky'),
    listDir: () => Promise.reject(new Error('EACCES'))
  }

  expect(await resolveFilePath('docs/plan.md', '/work/looky', fs)).toBeNull()
})

it('resolves nothing for a relative path when the session has no cwd', async () => {
  expect(await resolveFilePath('docs/plan.md', '', io())).toBeNull()
})
