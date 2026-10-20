import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

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
  afterEach(cleanup)

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

  it('routes relative media links to the preview card, not the cwd-less inline player', async () => {
    const { container } = render(<MarkdownTextContent isRunning={false} text="[shot](docs/a.png) [clip](out/demo.mp4)" />)

    await screen.findByText('a.png')
    expect(screen.getAllByRole('button', { name: 'Open preview' })).toHaveLength(2)
    expect(container.querySelector('img, video')).toBeNull()
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
