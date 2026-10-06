import type { ToolCallMessagePartProps } from '@assistant-ui/react'
import { QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { atom } from 'nanostores'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { type SessionView, SessionViewProvider } from '@/app/chat/session-view'
import { I18nProvider } from '@/i18n'
import { queryClient } from '@/lib/query-client'
import { $pinnedSessionIds } from '@/store/layout'
import { setModelPreset } from '@/store/model-presets'
import { $visibleModels } from '@/store/model-visibility'
import {
  $spawnTaskChips,
  resetSpawnTaskStoreForTests,
  setSpawnTaskChoice,
  setSpawnTaskLauncher,
  spawnTaskChoiceScope
} from '@/store/spawn-task'

import { SpawnTaskTool } from './spawn-task-tool'

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
  result: { status: 'offered', success: true },
  status: { type: 'complete' },
  toolCallId: 'spawn-call-1',
  toolName: 'spawn_task',
  type: 'tool-call'
}

function view(): SessionView {
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
    $storedId: atom('stored-parent'),
    $turnStartedAt: atom(null),
    kind: 'primary'
  }
}

function renderChip(props: ToolCallMessagePartProps = PROPS) {
  return render(
    <QueryClientProvider client={queryClient}>
      <I18nProvider configClient={null} initialLocale="en">
        <SessionViewProvider value={view()}>
          <SpawnTaskTool {...props} />
        </SessionViewProvider>
      </I18nProvider>
    </QueryClientProvider>
  )
}

const launcher = vi.fn()

beforeEach(() => {
  window.localStorage.clear()
  resetSpawnTaskStoreForTests()
  $pinnedSessionIds.set([])
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
        lineageId: 'stored-parent',
        ownerStoredSessionId: 'stored-parent',
        prompt: ARGS.prompt,
        title: ARGS.title,
        toolCallId: 'spawn-call-1'
      },
      { effort: 'low', fast: false, mode: 'tab', model: 'gpt-5.5-mini', pin: false, provider: 'openai' }
    )
    expect(await screen.findByText('Started in a new session')).toBeTruthy()
    expect($spawnTaskChips.get()['stored-parent::spawn-call-1']).toEqual({
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

  it('a dismissed chip stays dismissed and never launches', () => {
    renderChip()

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))

    expect(screen.getByText('Dismissed side task: Flaky login test')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Start in new tab' })).toBeNull()
    expect(launcher).not.toHaveBeenCalled()
  })
})
