import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { HermesGateway } from '@/hermes'
import { clearSessionDraft, mainComposerScope } from '@/store/composer'

import type { QueueEditState } from '../composer-utils'
import { caretOffsetInEditor } from '../rich-editor'

import { useComposerDraft } from './use-composer-draft'
import { resetSessionGhosts, useComposerGhost } from './use-composer-ghost'

const mockComposerApi = { setText: vi.fn() }

vi.mock('@assistant-ui/react', () => ({
  useAui: () => ({ composer: () => mockComposerApi }),
  useAuiState: (selector: (state: { composer: { text: string } }) => unknown) => selector({ composer: { text: '' } }),
  useComposerRuntime: () => ({
    getState: () => ({ text: '' }),
    subscribe: () => () => undefined
  })
}))

const GHOST = 'run the tests'

const gateway = {
  request: vi.fn(async () => ({ candidates: [{ kind: 'prediction', text: GHOST }], history_version: 1 }))
} as unknown as HermesGateway

// The real draft hook and the real ghost hook on a real contentEditable, wired the way ChatBar
// wires them. `writer` picks what accepted text is written with, so the test can also show why
// the composer-state setter alone is the wrong one.
function Harness({
  busy,
  sessionId = 'session-A',
  typed,
  writer
}: {
  busy: boolean
  sessionId?: string
  /** What the user has typed, as the composer state reports it (the AUI state is mocked empty). */
  typed?: string
  writer: 'loadIntoComposer' | 'setComposerText'
}) {
  const draft = useComposerDraft({
    activeQueueSessionKey: sessionId,
    focusKey: null,
    inputDisabled: false,
    queueEditRef: { current: null as QueueEditState | null },
    sessionId
  })

  const ghost = useComposerGhost({
    busy,
    gateway,
    hasText: typed === undefined ? draft.hasText : typed !== '',
    isEditorEmpty: () => (draft.editorRef.current?.textContent ?? '') === '',

    placeholder: 'Ask anything',
    sessionId,
    trigger: null,
    writeDraft: text =>
      writer === 'loadIntoComposer'
        ? draft.loadIntoComposer(text, mainComposerScope.$attachments.get())
        : draft.setComposerText(text)
  })

  return (
    <div
      contentEditable
      data-placeholder={ghost.placeholder}
      data-testid="editor"
      onKeyDown={event => void ghost.handleGhostKeyDown(event)}
      ref={draft.editorRef}
      suppressContentEditableWarning
    />
  )
}

async function showGhost(writer: 'loadIntoComposer' | 'setComposerText') {
  const view = render(<Harness busy writer={writer} />)
  const editor = view.getByTestId('editor')

  // The user is in the composer when the turn ends: that is the case that matters.
  act(() => editor.focus())
  expect(document.activeElement).toBe(editor)

  view.rerender(<Harness busy={false} writer={writer} />)
  await waitFor(() => expect(editor.getAttribute('data-placeholder')).toBe(GHOST))

  return editor
}

describe('accepting the ghost reply in a focused composer', () => {
  afterEach(() => {
    cleanup()
    mainComposerScope.clear()
    clearSessionDraft('session-A')
    clearSessionDraft('session-B')
    resetSessionGhosts()
    vi.clearAllMocks()
  })

  it.each(['Tab', 'ArrowRight'])('%s puts the suggestion into the editor with the caret at its end', async key => {
    const editor = await showGhost('loadIntoComposer')

    const notPrevented = fireEvent.keyDown(editor, { key })

    expect(notPrevented).toBe(false) // Tab must not move focus, Right arrow has nothing to move over
    expect(editor.textContent).toBe(GHOST)
    expect(caretOffsetInEditor(editor)).toBe(GHOST.length)
    expect(mockComposerApi.setText).toHaveBeenCalledWith(GHOST)
  })

  it('a modified key or another key leaves the ghost alone', async () => {
    const editor = await showGhost('loadIntoComposer')

    fireEvent.keyDown(editor, { key: 'ArrowRight', shiftKey: true })
    fireEvent.keyDown(editor, { key: 'ArrowLeft' })

    expect(editor.textContent).toBe('')
    expect(editor.getAttribute('data-placeholder')).toBe(GHOST)
  })

  it.each(['Tab', 'ArrowRight'])('%s never replaces text typed in the same frame', async key => {
    const editor = await showGhost('loadIntoComposer')

    // The DOM already holds a character, but no input event has reached the composer state yet,
    // so the ghost is still "visible": exactly the window between a keystroke and the next render.
    editor.textContent = 'f'
    const notPrevented = fireEvent.keyDown(editor, { key })

    expect(notPrevented).toBe(true) // the key keeps its native meaning
    expect(editor.textContent).toBe('f')
    expect(mockComposerApi.setText).not.toHaveBeenCalledWith(GHOST)
  })

  it.each(['Tab', 'ArrowRight'])('the ghost comes back after typed text is deleted, and %s accepts it', async key => {
    const view = render(<Harness busy writer="loadIntoComposer" />)
    const editor = view.getByTestId('editor')

    act(() => editor.focus())
    view.rerender(<Harness busy={false} writer="loadIntoComposer" />)
    await waitFor(() => expect(editor.getAttribute('data-placeholder')).toBe(GHOST))

    // The user starts a message of their own: the ghost hides.
    editor.textContent = 'fi'
    view.rerender(<Harness busy={false} typed="fi" writer="loadIntoComposer" />)
    expect(editor.getAttribute('data-placeholder')).toBe('Ask anything')

    // ... and deletes it again: the turn's suggestion is offered once more.
    editor.textContent = ''
    view.rerender(<Harness busy={false} typed="" writer="loadIntoComposer" />)
    expect(editor.getAttribute('data-placeholder')).toBe(GHOST)

    expect(fireEvent.keyDown(editor, { key })).toBe(false)
    expect(editor.textContent).toBe(GHOST)
  })

  it('an accepted suggestion that is deleted again is offered again', async () => {
    const view = render(<Harness busy writer="loadIntoComposer" />)
    const editor = view.getByTestId('editor')

    act(() => editor.focus())
    view.rerender(<Harness busy={false} writer="loadIntoComposer" />)
    await waitFor(() => expect(editor.getAttribute('data-placeholder')).toBe(GHOST))

    fireEvent.keyDown(editor, { key: 'Tab' })
    view.rerender(<Harness busy={false} typed={GHOST} writer="loadIntoComposer" />)
    expect(editor.getAttribute('data-placeholder')).toBe('Ask anything')

    editor.textContent = ''
    view.rerender(<Harness busy={false} typed="" writer="loadIntoComposer" />)
    expect(editor.getAttribute('data-placeholder')).toBe(GHOST)
  })

  it('switching to another session and back keeps the suggestion', async () => {
    await showGhost('loadIntoComposer')

    // Session A's composer goes away and one for session B takes its place.
    cleanup()
    const b = render(<Harness busy={false} sessionId="session-B" writer="loadIntoComposer" />)
    expect(b.getByTestId('editor').getAttribute('data-placeholder')).toBe('Ask anything')

    // A composer remounted on session A (another pane, or a re-targeted one) finds it again.
    b.rerender(<Harness busy={false} sessionId="session-A" writer="loadIntoComposer" />)
    const back = b.getByTestId('editor')
    expect(back.getAttribute('data-placeholder')).toBe(GHOST)

    act(() => back.focus())
    expect(fireEvent.keyDown(back, { key: 'Tab' })).toBe(false)
    expect(back.textContent).toBe(GHOST)
  })

  it('a reply that lands after switching away is kept for the session that asked', async () => {
    let resolve: (value: unknown) => void = () => undefined
    vi.mocked(gateway.request).mockImplementationOnce(() => new Promise(r => (resolve = r)) as never)

    const view = render(<Harness busy writer="loadIntoComposer" />)
    view.rerender(<Harness busy={false} writer="loadIntoComposer" />)
    view.rerender(<Harness busy={false} sessionId="session-B" writer="loadIntoComposer" />)

    await act(async () => resolve({ candidates: [{ kind: 'prediction', text: GHOST }], history_version: 1 }))
    const editor = view.getByTestId('editor')
    expect(editor.getAttribute('data-placeholder')).toBe('Ask anything') // not shown in B

    view.rerender(<Harness busy={false} sessionId="session-A" writer="loadIntoComposer" />)
    expect(editor.getAttribute('data-placeholder')).toBe(GHOST)
  })

  it('switching from a busy session to an idle one does not ask for a prediction', () => {
    const view = render(<Harness busy writer="loadIntoComposer" />)

    view.rerender(<Harness busy={false} sessionId="session-B" writer="loadIntoComposer" />)

    expect(gateway.request).not.toHaveBeenCalled()
  })

  it('Escape dismisses the ghost without writing anything', async () => {
    const editor = await showGhost('loadIntoComposer')

    fireEvent.keyDown(editor, { key: 'Escape' })

    expect(editor.textContent).toBe('')
    expect(editor.getAttribute('data-placeholder')).toBe('Ask anything')
  })

  it('the composer-state setter alone leaves the focused editor blank (why the draft write path is required)', async () => {
    const editor = await showGhost('setComposerText')

    fireEvent.keyDown(editor, { key: 'ArrowRight' })

    // The ghost is gone and the state holds the text, but nothing was painted: the bug as it shipped.
    expect(mockComposerApi.setText).toHaveBeenCalledWith(GHOST)
    expect(editor.textContent).toBe('')
  })
})
