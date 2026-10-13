import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { type ChatMessage, chatMessageText, toChatMessages } from '@/lib/chat-messages'
import {
  persistInFlightTurnState,
  readInFlightTurnJournal,
  recoverInFlightTurnJournal,
  resetInFlightTurnJournalStateForTests
} from '@/lib/inflight-turn-journal'
import type { SessionMessage } from '@/types/hermes'

// A long desktop session duplicated a finished answer at the end of the
// transcript once a newer prompt had been sent: the stale in-flight journal of
// the finished turn (assistant-stream-* bubbles, no rowId) was appended after
// the newer turn instead of being retired (#126091).

const SESSION = 'long-session'

const toolRound = (id: number, text: string): SessionMessage[] => [
  {
    id,
    role: 'assistant',
    content: text,
    timestamp: id,
    tool_calls: [{ id: `toolu_${id}`, type: 'function', function: { name: 'terminal', arguments: '{}' } }]
  },
  { id: id + 1, role: 'tool', content: 'ok', timestamp: id + 1, tool_call_id: `toolu_${id}` }
]

function journal(messages: ChatMessage[]) {
  persistInFlightTurnState({
    awaitingResponse: false,
    busy: true,
    messages,
    storedSessionId: SESSION,
    streamId: messages.at(-1)?.id ?? null,
    turnStartedAt: 1000
  })
  vi.advanceTimersByTime(400)
}

const summary = (rows: ChatMessage[]) => rows.map(row => `${row.role}:${chatMessageText(row)}`)

beforeEach(() => {
  vi.useFakeTimers()
  localStorage.clear()
  resetInFlightTurnJournalStateForTests()
})

afterEach(() => {
  resetInFlightTurnJournalStateForTests()
  localStorage.clear()
  vi.useRealTimers()
})

describe('stale journal of a finished tool turn after a newer prompt', () => {
  it('retires a finished tool turn whose pasted prompt hydrated with a different attachment path', () => {
    // A long prompt pasted in the composer: the live bubble holds the client's
    // chip ref, the persisted row the attachment path the backend stored.
    const paste = '@file:/Users/u/.hermes/attachments/pasted_content_2026-09-27_14-33-21-009_415aa0.txt'

    const base = toChatMessages([
      { id: 1, role: 'user', content: 'run the review', timestamp: 1 },
      { id: 2, role: 'assistant', content: 'Started it in the background.', timestamp: 2 },
      {
        id: 10,
        role: 'user',
        content: `${paste}\n\nroll it out\n\n--- Attached Context ---\n\n📄 ${paste} (769 tokens)\n\`\`\`\nplan\n\`\`\``,
        timestamp: 10
      },
      ...toolRound(11, 'Reading the plan.'),
      ...toolRound(13, 'Checking the diff.'),
      { id: 15, role: 'assistant', content: 'Rolled out.', timestamp: 15 },
      { id: 16, role: 'user', content: 'ready to ship?', timestamp: 16 }
    ])

    journal([
      {
        id: 'user-1790715585516-kliw03',
        rowId: 10,
        role: 'user',
        attachmentRefs: ['@file:pasted-text-1.txt'],
        parts: [{ type: 'text', text: 'roll it out' }]
      },
      {
        id: 'assistant-stream-1790715593574-25',
        role: 'assistant',
        interim: true,
        parts: [
          { type: 'text', text: 'Reading the plan.' },
          { type: 'tool-call', toolCallId: 'toolu_11', toolName: 'terminal', args: {} }
        ]
      },
      {
        id: 'assistant-stream-1790715594065-26',
        role: 'assistant',
        interim: true,
        parts: [
          { type: 'text', text: 'Checking the diff.' },
          { type: 'tool-call', toolCallId: 'toolu_13', toolName: 'terminal', args: {} }
        ]
      },
      {
        id: 'assistant-stream-1790715615002-27',
        role: 'assistant',
        pending: true,
        parts: [{ type: 'text', text: 'Rolled out.' }]
      }
    ])

    const result = recoverInFlightTurnJournal(SESSION, base, { keepPending: false })

    expect(summary(result.messages)).toEqual(summary(base))
    expect(result.messages).toBe(base)
    expect(result.applied).toBe(false)
    expect(result.caughtUp).toBe(true)
    expect(readInFlightTurnJournal(SESSION)).toBeNull()
  })

  // ColdSlither's minimal case from #126091: the journaled prompt never
  // persisted (no rowId), so the answer must be retired by coverage alone.
  // #128599 deliberately leaves unidentified prompts to content matching, so
  // this still duplicates; flip to it() once the coverage window is fixed.
  it.fails('does not re-append a committed answer behind a stray journaled prompt', () => {
    journal([
      {
        id: 'user-inflight-ae4fe114',
        role: 'user',
        parts: [{ type: 'text', text: 'a stray user bubble that never persisted' }]
      },
      { id: 'assistant-stream-1', role: 'assistant', parts: [{ type: 'text', text: 'the committed answer' }] }
    ])

    const base: ChatMessage[] = [
      { id: 'db-u1', role: 'user', parts: [{ type: 'text', text: 'the real prompt' }] },
      { id: 'db-a1', role: 'assistant', rowId: 42, parts: [{ type: 'text', text: 'the committed answer' }] },
      { id: 'db-u2', role: 'user', parts: [{ type: 'text', text: 'the next question' }] }
    ]

    const result = recoverInFlightTurnJournal(SESSION, base, { keepPending: false })

    expect(summary(result.messages)).toEqual(summary(base))
    expect(result.caughtUp).toBe(true)
  })
})
