import { AssistantRuntimeProvider, ExportedMessageRepository, type ThreadMessage } from '@assistant-ui/react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { useIncrementalExternalStoreRuntime } from '@/lib/incremental-external-store-runtime'
import { $previewTabs } from '@/store/preview'
import { $activeSessionId, $currentCwd } from '@/store/session'

import { assistantMessage, createdAt, stubThreadEnvironment, stubThreadViewportSize } from '../test-utils'

import { UserMessageText } from './user-message-text'

import { Thread } from '.'

stubThreadEnvironment()
stubThreadViewportSize()

const PASTE = '/home/u/.hermes/attachments/pasted_content_2026-10-18_09-46-21-616_22f3fd.txt'

beforeEach(() => {
  $previewTabs.set([])
  $activeSessionId.set(null)
  $currentCwd.set('/repo')
})

afterEach(() => {
  cleanup()
  $previewTabs.set([])
})

const openedPaths = () => $previewTabs.get().map(tab => tab.target.path)

function sentMessage(text: string, attachmentRefs: string[]): ThreadMessage {
  return {
    id: 'user-1',
    role: 'user',
    content: [{ type: 'text', text }],
    attachments: [],
    createdAt,
    metadata: { custom: { attachmentRefs } }
  } as ThreadMessage
}

function SentTurn({ message }: { message: ThreadMessage }) {
  const runtime = useIncrementalExternalStoreRuntime<ThreadMessage>({
    messageRepository: ExportedMessageRepository.fromArray([message, assistantMessage()]),
    isRunning: false,
    setMessages: () => {},
    onNew: async () => {},
    onEdit: async () => {},
    onCancel: async () => {},
    onReload: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread cwd={null} gateway={null} sessionId="session-1" />
    </AssistantRuntimeProvider>
  )
}

/**
 * A sent `@file:` chip opens its file in the preview rail, like the composer's
 * attachment chip did before send. A large paste is only reachable this way:
 * the bubble shows "Pasted content", never the text.
 */
describe('a sent @file: chip opens its file', () => {
  it('opens a paste-only turn from inside the bubble without opening the editor', async () => {
    const { container } = render(<SentTurn message={sentMessage('', [`@file:${PASTE}`])} />)

    const chip = await screen.findByRole('button', { name: 'Pasted content' })
    fireEvent.pointerDown(chip)
    fireEvent.click(chip)

    await waitFor(() => expect(openedPaths()).toEqual([PASTE]))
    expect(screen.queryByRole('textbox', { name: 'Edit message' })).toBeNull()
    // The bubble's press holds the viewport for an edit that never opens.
    expect(container.querySelector('[data-editing]')).toBeNull()
  })

  it('opens an attachment-row chip against the chat cwd', async () => {
    render(<SentTurn message={sentMessage('look at this', ['@file:`src/my notes.md`'])} />)

    fireEvent.click(await screen.findByTitle('src/my notes.md'))

    await waitFor(() => expect(openedPaths()).toEqual(['/repo/src/my notes.md']))
  })

  it('opens a chip inside the message text against the chat cwd', async () => {
    render(<UserMessageText text="before @file:src/a.ts after" />)

    fireEvent.click(screen.getByRole('button', { name: 'src/a.ts' }))

    await waitFor(() => expect(openedPaths()).toEqual(['/repo/src/a.ts']))
  })
})
