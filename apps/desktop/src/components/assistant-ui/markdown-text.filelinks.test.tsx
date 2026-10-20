import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { PRIMARY_SESSION_VIEW, SessionViewProvider } from '@/app/chat/session-view'
import { renderMediaTags } from '@/lib/chat-messages/parts'
import type * as Media from '@/lib/media'
import { $currentCwd } from '@/store/session'

const { resolveMediaPlaybackSrc } = vi.hoisted(() => ({ resolveMediaPlaybackSrc: vi.fn() }))

vi.mock('@/lib/media', async importOriginal => {
  const actual = await importOriginal<typeof Media>()
  resolveMediaPlaybackSrc.mockImplementation(actual.resolveMediaPlaybackSrc)

  return { ...actual, resolveMediaPlaybackSrc }
})

import { MarkdownTextContent } from './markdown-text'

// Regression for #82140: a plain filesystem href in assistant markdown
// (`[report](/home/user/report.md)`) rendered as a bare dead anchor —
// file:// is blocked in the renderer, and on a remote gateway the path
// isn't on this disk at all. Such links must route through the preview
// pipeline (PreviewAttachment → normalizeOrLocalPreviewTarget), which
// resolves the path at VIEW time against the session's backend: local
// connections read the file directly, remote connections fetch it over the
// authenticated /api/fs bridge. Media-extension paths keep their inline
// player instead.
describe('MarkdownLink filesystem hrefs', () => {
  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
    $currentCwd.set('')
  })

  it('routes an absolute file path link through the preview attachment', async () => {
    render(<MarkdownTextContent isRunning={false} text="Wrote it: [report](/home/user/report.md)" />)

    // PreviewAttachment paints the filename + an Open preview button —
    // that's the view-time door, not a dead <a>.
    await screen.findByText('report.md')
    expect(screen.getByRole('button', { name: 'Open preview' })).toBeTruthy()
    expect(document.querySelector('a[href="/home/user/report.md"]')).toBeNull()
  })

  it('routes file:// and ~/ links the same way', async () => {
    render(
      <MarkdownTextContent isRunning={false} text={'See [notes](file:///srv/data/notes.txt) and [todo](~/todo.md)'} />
    )

    await screen.findByText('notes.txt')
    await screen.findByText('todo.md')
    expect(screen.getAllByRole('button', { name: 'Open preview' })).toHaveLength(2)
  })

  it('routes angle-bracket file links whose path contains spaces', async () => {
    // CommonMark requires `<...>` destinations for paths with spaces; the
    // old FILE_LINK_RE charset `[^\)\s]*` couldn't match them, so the link
    // fell through to harden and rendered as "label [blocked]".
    render(<MarkdownTextContent isRunning={false} text={'See [notes](<~/My Notes/todo with spaces.md>)'} />)

    await screen.findByText('todo with spaces.md')
    expect(screen.getByRole('button', { name: 'Open preview' })).toBeTruthy()
    expect(screen.queryByText(/blocked/)).toBeNull()
  })

  it('routes percent-encoded file links through the same preview pipeline', async () => {
    // Markdown renderers emit the href percent-encoded; the renderer keeps
    // the encoded form in the card label, and the electron side retries the
    // decoded on-disk path when the file is opened.
    render(<MarkdownTextContent isRunning={false} text={'See [notes](~/My%20Notes/todo%20with%20spaces.md)'} />)

    await screen.findByText('todo%20with%20spaces.md')
    expect(screen.getByRole('button', { name: 'Open preview' })).toBeTruthy()
  })

  it('renders a media player for a media-extension path link', async () => {
    const { container } = render(<MarkdownTextContent isRunning={false} text="[clip](/tmp/demo.mp4)" />)

    await waitFor(() => expect(container.querySelector('video')).not.toBeNull())
    expect(container.querySelector('a[href="/tmp/demo.mp4"]')).toBeNull()
  })

  it('routes relative file links through the preview pipeline', async () => {
    // Models cite repo files relative to the session cwd; Streamdown's
    // hardener can't parse `.planning/x` without a base and painted
    // "label [blocked]". PreviewAttachment resolves them against the cwd.
    render(
      <MarkdownTextContent
        isRunning={false}
        text={'[plan](.planning/plan.html), [guide](docs/guide.md), [up](../notes.md) and [here](./todo.md)'}
      />
    )

    await screen.findByText('plan.html')
    expect(screen.getAllByRole('button', { name: 'Open preview' })).toHaveLength(4)
    expect(screen.queryByText(/blocked/)).toBeNull()
  })

  it('hands the raw relative target to the card with the session cwd', async () => {
    // The card's Open resolves the target itself against the session cwd, so
    // the old HOME/attachments fallback in the native normalizer still sees a
    // relative path; Download resolves against the session on the backend.
    const normalizePreviewTarget = vi.fn().mockResolvedValue(null)
    vi.stubGlobal('hermesDesktop', { normalizePreviewTarget })
    $currentCwd.set('/repo/app')

    render(
      <MarkdownTextContent
        isRunning={false}
        text={`${renderMediaTags('MEDIA:.hermes/attachments/report.md')} and [guide](docs/guide.md)`}
      />
    )

    await waitFor(() => expect(normalizePreviewTarget).toHaveBeenCalledTimes(2))
    expect(normalizePreviewTarget.mock.calls.map(call => call.slice(0, 2))).toEqual([
      ['.hermes/attachments/report.md', '/repo/app'],
      ['docs/guide.md', '/repo/app']
    ])
  })

  it('plays a relative media link inline from the session cwd', async () => {
    $currentCwd.set('/repo')
    const { container } = render(<MarkdownTextContent isRunning={false} text="[clip](out/demo.mp4)" />)

    await waitFor(() => expect(container.querySelector('video')).not.toBeNull())
    expect(screen.queryByRole('button', { name: 'Open preview' })).toBeNull()
    expect(resolveMediaPlaybackSrc).toHaveBeenCalledWith('/repo/out/demo.mp4')
  })

  it('plays inline media from the cwd of the panel that rendered the transcript', async () => {
    // The media stream has no cwd of its own, so the path must already be
    // absolute — resolved against THIS pane, not the primary chat.
    $currentCwd.set('/primary')

    const { container } = render(
      <SessionViewProvider value={{ ...PRIMARY_SESSION_VIEW, $cwd: atom('/panel') }}>
        <MarkdownTextContent isRunning={false} text="[clip](out/demo.mp4)" />
      </SessionViewProvider>
    )

    await waitFor(() => expect(container.querySelector('video')).not.toBeNull())
    expect(resolveMediaPlaybackSrc).toHaveBeenCalledWith('/panel/out/demo.mp4')
  })

  it('routes angle-bracket and non-ASCII relative paths, not ~user/', async () => {
    render(
      <MarkdownTextContent
        isRunning={false}
        text={'[a](<docs/My Notes.md>) [b](документы/план.md) [c](~user/docs/a.md)'}
      />
    )

    await screen.findByText('My Notes.md')
    await screen.findByText('план.md')
    expect(screen.getAllByRole('button', { name: 'Open preview' })).toHaveLength(2)
  })

  it('routes an angle-bracket relative path whose first directory has a space', async () => {
    // escapeUnknownHtmlLikeTags used to read `<My Docs/report.md>` as an
    // unknown tag and escape it before file routing ever saw the link.
    render(<MarkdownTextContent isRunning={false} text={'[a](<My Docs/report.md>)'} />)

    await screen.findByText('report.md')
    expect(screen.getByRole('button', { name: 'Open preview' })).toBeTruthy()
    expect(screen.queryByText(/blocked/)).toBeNull()
  })

  it('routes a relative path with a combining mark in the first segment', async () => {
    // `é` written decomposed (e + U+0301) is the same directory name as the
    // NFC spelling, and must not read as a host.
    render(<MarkdownTextContent isRunning={false} text={'[a](e\u0301/x.md)'} />)

    await screen.findByText('x.md')
    expect(screen.getByRole('button', { name: 'Open preview' })).toBeTruthy()
    expect(screen.queryByText(/blocked/)).toBeNull()
  })

  it('leaves anchors, web, mail and bare host links out of the preview pipeline', () => {
    render(
      <MarkdownTextContent
        isRunning={false}
        text={
          '[frag](#section-2) and [site](https://example.com) and [mail](mailto:a@b.c) and [host](example.com) and [page](example.com/docs/a.md)'
        }
      />
    )

    expect(screen.queryByRole('button', { name: 'Open preview' })).toBeNull()
    expect(document.querySelector('a[href="#section-2"]')).not.toBeNull()
  })
})
