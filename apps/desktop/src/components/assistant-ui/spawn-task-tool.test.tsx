import type { ToolCallMessagePartProps } from '@assistant-ui/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { type SessionView, SessionViewProvider } from '@/app/chat/session-view'
import { I18nProvider } from '@/i18n'
import { toChatMessages } from '@/lib/chat-messages/hydration'
import { queryClient } from '@/lib/query-client'
import { $pinnedSessionIds } from '@/store/layout'
import { setModelPreset } from '@/store/model-presets'
import { $visibleModels } from '@/store/model-visibility'
import { $sessionDotStateById } from '@/store/session-dot-state'
import { $sessionTiles } from '@/store/session-states'
import {
  $spawnTaskChips,
  resetSpawnTaskStoreForTests,
  setSpawnTaskChoice,
  setSpawnTaskLauncher,
  spawnTaskChipKey,
  spawnTaskChoiceScope
} from '@/store/spawn-task'

import { SpawnTaskTool } from './spawn-task-tool'

const dotStates = vi.hoisted(() => ({ value: {} as Record<string, string> }))

vi.mock('@/store/session-dot-state', async importOriginal => {
  const { atom: makeAtom } = await import('nanostores')
  const real = await importOriginal<Record<string, unknown>>()
  const $sessionDotStateById = makeAtom(dotStates.value)

  return { ...real, $sessionDotStateById }
})

const getGlobalModelOptions = vi.fn()

vi.mock('@/hermes', () => ({
  getGlobalModelOptions: (...args: unknown[]) => getGlobalModelOptions(...args),
  getLocalModelsJobs: vi.fn().mockResolvedValue({ jobs: [] }),
  getLocalModelsStatus: vi.fn().mockResolvedValue({ loading: {} }),
  setApiRequestProfile: vi.fn()
}))

vi.mock('@/components/assistant-ui/tool/fallback', () => ({
  ToolFallback: () => <div data-testid="tool-fallback" />
}))

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
})

const ARGS = { prompt: 'Fix tests/test_login.py flake: retry on 429.', title: 'Flaky login test', tldr: 'Fails on CI' }

const PROPS: ToolCallMessagePartProps = {
  addResult: vi.fn(),
  args: ARGS,
  argsText: JSON.stringify(ARGS),
  isError: false,
  respondToApproval: vi.fn(),
  resume: vi.fn(),
  result: { status: 'offered', success: true, title: 'Flaky login test' },
  status: { type: 'complete' },
  toolCallId: 'spawn-call-1',
  toolName: 'spawn_task',
  type: 'tool-call'
}

function view(storedId: null | string = 'stored-parent'): SessionView {
  return {
    $awaitingResponse: atom(false),
    $busy: atom(false),
    $cwd: atom('/repo'),
    $fast: atom(false),
    $lastVisibleIsUser: atom(false),
    $messages: atom([]),
    $messagesEmpty: atom(false),
    $model: atom('session-model'),
    $provider: atom('anthropic'),
    $reasoningEffort: atom(''),
    $reasoningEffortPending: atom(false),
    $reasoningEffortWire: atom(''),
    $runtimeId: atom('runtime-1'),
    $storedId: atom(storedId),
    $turnStartedAt: atom(null),
    kind: 'primary'
  }
}

function renderChip(props: ToolCallMessagePartProps = PROPS, storedId: null | string = 'stored-parent') {
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider configClient={null} initialLocale="en">
        <SessionViewProvider value={view(storedId)}>
          <SpawnTaskTool {...props} />
        </SessionViewProvider>
      </I18nProvider>
    </QueryClientProvider>
  )
}

/** The mocked atom is a plain one, whose `set` writes it directly; `dotStates.value` is only
 *  its seed value, so the atom itself is what has to be cleared between tests. */
function setDotStates(states: Record<string, string>) {
  act(() => ($sessionDotStateById as unknown as { set: (v: unknown) => void }).set(states))
}

const launcher = vi.fn()

beforeEach(() => {
  window.localStorage.clear()
  resetSpawnTaskStoreForTests()
  setDotStates({})
  $pinnedSessionIds.set([])
  $sessionTiles.set([])
  queryClient.clear()
  queryClient.setDefaultOptions({ queries: { ...queryClient.getDefaultOptions().queries, retry: false } })
  $visibleModels.set(null)
  getGlobalModelOptions.mockResolvedValue({
    providers: [
      {
        capabilities: { 'gpt-5.5': { fast: false, reasoning: true } },
        models: ['gpt-5.5', 'gpt-5.5-mini'],
        name: 'OpenAI',
        slug: 'openai'
      }
    ]
  })
  launcher.mockReset()
  launcher.mockResolvedValue({ storedSessionId: 'stored-child' })
  setSpawnTaskLauncher(launcher)
})

afterEach(() => {
  cleanup()
  $sessionTiles.set([])
  setSpawnTaskLauncher(null)
  queryClient.clear()
})

describe('the spawn-task chip', () => {
  it('opens on the model, effort and mode the user launched the previous chip with', () => {
    setSpawnTaskChoice(spawnTaskChoiceScope(null, 'default'), {
      effort: 'high',
      fast: false,
      mode: 'worktree',
      model: 'gpt-5.5',
      pin: true,
      provider: 'openai'
    })
    renderChip()

    expect(screen.getByText('Flaky login test')).toBeTruthy()
    expect(screen.getByRole('checkbox', { name: 'Pin' }).getAttribute('data-state')).toBe('checked')
    expect(screen.getByRole('button', { name: 'Model for the new session' }).textContent).toMatch(/GPT-5\.5.*High/i)
    expect(screen.getByRole('button', { name: 'Start in worktree' }).className).toMatch(/bg-primary/)
  })

  it('launches with the model picked on the chip, seeded with that model’s remembered effort', async () => {
    setModelPreset('openai', 'gpt-5.5-mini', { effort: 'low' })
    renderChip()

    const trigger = screen.getByRole('button', { name: 'Model for the new session' })
    fireEvent.keyDown(trigger, { key: 'Enter' })

    const search = await screen.findByRole('textbox', { name: 'Search models' })
    fireEvent.change(search, { target: { value: 'mini' } })
    await waitFor(() => expect(screen.getByRole('menu').textContent).toMatch(/GPT-5\.5-mini/))
    fireEvent.keyDown(search, { key: 'Enter' })

    const trigger2 = screen.getByRole('button', { name: 'Model for the new session' })
    await waitFor(() => expect(trigger2.textContent).toMatch(/GPT-5\.5-mini.*Low/i))
    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))

    await waitFor(() => expect(launcher).toHaveBeenCalledTimes(1))
    expect(launcher).toHaveBeenCalledWith(
      {
        cwd: '/repo',
        ownerStoredSessionId: 'stored-parent',
        prompt: ARGS.prompt,
        title: ARGS.title,
        toolCallId: 'spawn-call-1'
      },
      { effort: 'low', fast: false, mode: 'tab', model: 'gpt-5.5-mini', pin: false, provider: 'openai' }
    )
    expect(await screen.findByText('Started in a new session')).toBeTruthy()
    expect($spawnTaskChips.get()[spawnTaskChipKey({ prompt: ARGS.prompt, title: ARGS.title, toolCallId: 'spawn-call-1' })]).toEqual({
      state: 'launched',
      storedSessionId: 'stored-child'
    })
  })

  it('ticking Pin pins the session the chip starts', async () => {
    renderChip()

    fireEvent.click(screen.getByRole('checkbox', { name: 'Pin' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))

    await waitFor(() => expect($pinnedSessionIds.get()).toContain('stored-child'))
    expect(launcher).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ pin: true }))
  })

  // Only a success receipt makes a chip. A cancelled / timed-out call (the
  // model was told it was skipped), a pending one, or a receipt without a
  // validated title must never become launchable — and never with the raw
  // argument title, which skips every tool-side check (e.g. "Bot Chat").
  it.each([
    ['cancelled', '[Tool execution cancelled — spawn_task was skipped due to user interrupt]'],
    ['timed out', "Error executing tool 'spawn_task': timed out"],
    ['pending', undefined],
    ['no validated title', { status: 'offered', success: true }],
    ['not a success receipt', { status: 'offered', title: 'Flaky login test' }]
  ])('a %s call renders no launchable chip', (_label, result) => {
    renderChip({ ...PROPS, args: { ...ARGS, title: 'Bot Chat' }, result })

    expect(screen.queryByRole('button', { name: 'Start in new tab' })).toBeNull()
  })

  // The gateway carries a tool's own refusal inside `result` with isError
  // false; a refused offer must render as the failed row, never as a chip.
  it('a refused offer renders as an error row, not a launchable chip', () => {
    renderChip({ ...PROPS, result: { error: 'spawn_task needs a short title for the chip.' } })

    expect(screen.getByTestId('tool-fallback')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Start in new tab' })).toBeNull()
  })

  it('a chip mounted before the launcher registers comes alive when it does', async () => {
    setSpawnTaskLauncher(null)
    renderChip()

    const start = screen.getByRole('button', { name: 'Start in new tab' }) as HTMLButtonElement
    expect(start.disabled).toBe(true)

    act(() => setSpawnTaskLauncher(launcher))
    await waitFor(() => expect(start.disabled).toBe(false))
  })

  // The tool cleans and cuts the title to the store's limit; the chip must
  // launch with THAT title, not the raw model argument the store would refuse.
  it('launches with the title the tool validated, not the raw argument', async () => {
    renderChip({
      ...PROPS,
      args: { ...ARGS, title: `  ${'x'.repeat(212)}  ` },
      result: { success: true, title: 'x'.repeat(100) }
    })

    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))

    await waitFor(() =>
      expect(launcher).toHaveBeenCalledWith(expect.objectContaining({ title: 'x'.repeat(100) }), expect.anything())
    )
  })

  // `session.resume` / `/compress` hand over the gateway's compact projection
  // (`_history_to_messages`), not raw rows: the chip must still render from it.
  it('renders from the compact resume/compress projection of the transcript', () => {
    const receipt = JSON.stringify({ status: 'offered', success: true, title: ARGS.title })

    const part = toChatMessages([
      { role: 'user', text: 'go' },
      { args: ARGS, content: receipt, context: 'Fix tests', name: 'spawn_task', role: 'tool', tool_call_id: 'call_1' }
    ] as never)
      .flatMap(message => message.parts)
      .find(candidate => (candidate as { type: string }).type === 'tool-call') as unknown as ToolCallMessagePartProps

    renderChip({ ...PROPS, ...part, toolName: 'spawn_task' })

    expect(screen.getByRole('button', { name: 'Start in new tab' })).toBeTruthy()
  })

  it('an offer that wants the result launches with it and says where it will arrive', async () => {
    $sessionTiles.set([{ ownerRoute: { connectionId: 'local', profile: 'default' }, storedSessionId: 'stored-parent' } as never])
    renderChip({ ...PROPS, result: { notify_on_done: true, success: true, title: ARGS.title } })

    expect(screen.getByText('The result will come back to this chat')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))

    await waitFor(() =>
      expect(launcher).toHaveBeenCalledWith(
        expect.objectContaining({ notify: true, ownerStoredSessionId: 'stored-parent' }),
        expect.anything()
      )
    )
  })

  it('an offer that does not want the result never asks for it', async () => {
    renderChip()

    expect(screen.queryByText('The result will come back to this chat')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))

    await waitFor(() => expect(launcher).toHaveBeenCalled())
    expect(launcher.mock.calls[0]![0]).not.toMatchObject({ notify: true })
  })

  // A chat with no stored id yet has no session to report to: the launch would drop
  // `spawn_report_to`, so the promise must not be made (Д4).
  it('an offer that wants the result promises nothing without a stored owner id', async () => {
    renderChip({ ...PROPS, result: { notify_on_done: true, success: true, title: ARGS.title } }, null)

    expect(screen.queryByText('The result will come back to this chat')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))

    await waitFor(() => expect(launcher).toHaveBeenCalled())
    expect(launcher.mock.calls[0]![0]).not.toMatchObject({ notify: true })
  })

  // A stored id that does not resolve (truncated page, another window, an evicted hint) is
  // NOT a delivery: the launcher sends `spawn_report_to` only for a resolved owner, so the
  // chip must gate its promise on the very same condition (Д5) — not just a non-empty id.
  it('an offer that wants the result promises nothing when the owner does not resolve', async () => {
    renderChip({ ...PROPS, result: { notify_on_done: true, success: true, title: ARGS.title } })

    expect(screen.queryByText('The result will come back to this chat')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))

    await waitFor(() => expect(launcher).toHaveBeenCalled())
    expect(launcher.mock.calls[0]![0]).not.toMatchObject({ notify: true })
  })

  it('a launched chip shows what its session is doing', async () => {
    renderChip()
    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))
    await waitFor(() => expect(screen.getByText('Started in a new session')).toBeTruthy())

    // `draft` is its own answer — the session exists but no turn has run. There is no
    // `idle` case: the dot-state map never holds an `idle` entry (idle is the absence of
    // a claim), so a resting child shows nothing rather than an invented label.
    const cases: [string, string][] = [
      ['draft', 'Starting'],
      ['working', 'Working'],
      ['stalled', 'Working'],
      ['needs-input', 'Needs your input'],
      ['unread', 'Finished — unread']
    ]

    for (const [state, label] of cases) {
      setDotStates({ 'stored-child': state })
      expect(screen.getByTestId('spawn-task-status').textContent).toBe(label)
    }
  })

  // Nothing has arrived for the child in THIS window — its tab was closed, the parent chat
  // lives in another window, the app was just reloaded. Inventing a state there is how the
  // chip came to say "Idle" while the session was working.
  it('says nothing about a child whose state this window does not know', async () => {
    renderChip()
    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))
    await waitFor(() => expect(screen.getByText('Started in a new session')).toBeTruthy())

    setDotStates({ 'some-other-session': 'working' })

    expect(screen.queryByTestId('spawn-task-status')).toBeNull()
  })

  // The dot-state map has no `idle` entry, so `status.idle` is gone. Should a mocked or
  // future map ever hand the chip an `idle` bucket, it must stay quiet, not print an
  // undefined label.
  it('says nothing for a child the state map reports as idle', async () => {
    renderChip()
    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))
    await waitFor(() => expect(screen.getByText('Started in a new session')).toBeTruthy())

    setDotStates({ 'stored-child': 'idle' })

    expect(screen.queryByTestId('spawn-task-status')).toBeNull()
  })

  it('a dismissed chip stays dismissed and never launches', () => {
    renderChip()

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))

    expect(screen.getByText('Dismissed side task: Flaky login test')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Start in new tab' })).toBeNull()
    expect(launcher).not.toHaveBeenCalled()
  })

  // The user expands "Show prompt" to edit the task before launching it: the
  // text they typed is what the new session receives, while the chip keeps the
  // identity of the offer it was made from (so it reads back as launched).
  it('launches the prompt the user edited, and shows the sent text read-only', async () => {
    renderChip()
    fireEvent.click(screen.getByRole('button', { name: 'Show prompt' }))

    const textarea = screen.getByRole('textbox', { name: 'Prompt for the new session' }) as HTMLTextAreaElement
    const edited = 'Fix tests/test_login.py flake: retry on 429, and log the attempt.'
    fireEvent.change(textarea, { target: { value: edited } })
    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))

    await waitFor(() => expect(launcher).toHaveBeenCalledTimes(1))
    expect(launcher).toHaveBeenCalledWith(
      expect.objectContaining({ editedPrompt: edited, prompt: ARGS.prompt, toolCallId: 'spawn-call-1' }),
      expect.anything()
    )

    // Keyed by the ORIGINAL text: the launched chip is found again (never re-armed).
    expect(
      $spawnTaskChips.get()[spawnTaskChipKey({ prompt: ARGS.prompt, title: ARGS.title, toolCallId: 'spawn-call-1' })]
    ).toEqual({ prompt: edited, state: 'launched', storedSessionId: 'stored-child' })

    await screen.findByText('Started in a new session')
    // What was actually sent, read-only — no field to edit a task already running.
    expect(screen.queryByRole('textbox', { name: 'Prompt for the new session' })).toBeNull()
    expect(screen.getByText(edited)).toBeTruthy()
  })

  it('offers Reset only once the text is edited, and restores the offered prompt', () => {
    renderChip()
    fireEvent.click(screen.getByRole('button', { name: 'Show prompt' }))

    const textarea = screen.getByRole('textbox', { name: 'Prompt for the new session' }) as HTMLTextAreaElement
    expect(textarea.value).toBe(ARGS.prompt)
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull()

    fireEvent.change(textarea, { target: { value: 'Something else entirely' } })
    expect(screen.getByRole('button', { name: 'Reset' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Reset' }))
    expect((screen.getByRole('textbox', { name: 'Prompt for the new session' }) as HTMLTextAreaElement).value).toBe(
      ARGS.prompt
    )
    expect(screen.queryByRole('button', { name: 'Reset' })).toBeNull()
  })

  it('keeps the edit when the prompt is collapsed and reopened', () => {
    renderChip()
    fireEvent.click(screen.getByRole('button', { name: 'Show prompt' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Prompt for the new session' }), {
      target: { value: 'Edited while expanded' }
    })

    fireEvent.click(screen.getByRole('button', { name: 'Hide prompt' }))
    fireEvent.click(screen.getByRole('button', { name: 'Show prompt' }))

    expect((screen.getByRole('textbox', { name: 'Prompt for the new session' }) as HTMLTextAreaElement).value).toBe(
      'Edited while expanded'
    )
  })

  it('sends the original prompt when the edit is blank', async () => {
    renderChip()
    fireEvent.click(screen.getByRole('button', { name: 'Show prompt' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Prompt for the new session' }), {
      target: { value: '   ' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Start in new tab' }))

    await waitFor(() => expect(launcher).toHaveBeenCalledTimes(1))
    expect(launcher.mock.calls[0]![0]).toMatchObject({ editedPrompt: '   ' })
    expect(
      $spawnTaskChips.get()[spawnTaskChipKey({ prompt: ARGS.prompt, title: ARGS.title, toolCallId: 'spawn-call-1' })]
    ).toEqual({ state: 'launched', storedSessionId: 'stored-child' })

    await screen.findByText('Started in a new session')
    expect(screen.getByText(ARGS.prompt)).toBeTruthy()
  })

  // A launched chip stored before the edit existed carries no sent text: it
  // falls back to the prompt it was offered with.
  it('shows the offered prompt on a chip launched before the sent text was stored', () => {
    window.localStorage.setItem(
      'hermes.desktop.spawn-task.chips',
      JSON.stringify({
        [spawnTaskChipKey({ prompt: ARGS.prompt, title: ARGS.title, toolCallId: 'spawn-call-1' })]: {
          state: 'launched',
          storedSessionId: 'stored-old'
        }
      })
    )
    resetSpawnTaskStoreForTests()
    renderChip()

    fireEvent.click(screen.getByRole('button', { name: 'Show prompt' }))

    expect(screen.getByText(ARGS.prompt)).toBeTruthy()
    expect(screen.queryByRole('textbox', { name: 'Prompt for the new session' })).toBeNull()
  })
})
