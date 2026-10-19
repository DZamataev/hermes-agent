/**
 * A Kanban card created from a Desktop chat subscribes that chat; finishing the
 * card must wake the same Desktop session with a notification turn, and the
 * agent must answer it in the same chat. The CLI subscription shape
 * (`notify-subscribe --platform tui --chat-id agent:main:local:dm:<sid>`) must
 * land there too.
 *
 * Prerequisite: `npm run build` must have been run so dist/ exists.
 */

import { spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { DatabaseSync } from 'node:sqlite'

import {
  KANBAN_CARD_CREATED_TEXT,
  KANBAN_CARD_TITLE,
  KANBAN_CARD_TRIGGER,
  MOCK_REPLY,
} from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const REPO_ROOT = path.resolve(import.meta.dirname, '../../..')

let fixture: MockBackendFixture | null = null
let localSessionId = ''

test.beforeAll(async () => {
  fixture = await setupMockBackend({
    extraConfig: ['toolsets:', '  - kanban', 'kanban:', '  dispatch_in_gateway: false', ''].join('\n'),
  })
  await waitForAppReady(fixture, 180_000)
  // Keep the run out of the operator's way: the window stays "visible" to
  // Playwright and the renderer, but transparent and click-through on screen.
  await fixture.app.evaluate(({ BrowserWindow }) => {
    for (const window of BrowserWindow.getAllWindows()) {
      window.setOpacity(0)
      window.setIgnoreMouseEvents(true)
    }
  })
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

function pythonBinary(): string {
  if (process.env.HERMES_DESKTOP_PYTHON) {
    return process.env.HERMES_DESKTOP_PYTHON
  }

  const suffix = process.platform === 'win32' ? ['Scripts', 'python.exe'] : ['bin', 'python']
  const candidate = ['.venv', 'venv'].map(dir => path.join(REPO_ROOT, dir, ...suffix)).find(file => fs.existsSync(file))

  if (!candidate) {
    throw new Error('Create the repo Python venv or set HERMES_DESKTOP_PYTHON before running this spec')
  }

  return candidate
}

/** The operator's CLI against the sandbox home, never the real ~/.hermes. */
function hermesCli(...args: string[]): string {
  const sandbox = fixture!.sandbox

  const env: Record<string, string> = {
    PATH: process.env.PATH ?? '',
    HERMES_HOME: sandbox.hermesHome,
    HOME: sandbox.root,
    PYTHONPATH: REPO_ROOT,
  }

  if (process.env.TMPDIR) {
    env.TMPDIR = process.env.TMPDIR
  }

  const result = spawnSync(pythonBinary(), ['-m', 'hermes_cli.main', ...args], { env, encoding: 'utf8', timeout: 120_000 })

  if (result.status !== 0) {
    throw new Error(`hermes ${args.join(' ')} failed (${result.status}): ${result.stderr || result.stdout}`)
  }

  return result.stdout
}

interface NotifySub {
  chat_id: string
  platform: string
  task_id: string
}

function notifySubs(): NotifySub[] {
  const db = new DatabaseSync(path.join(fixture!.sandbox.hermesHome, 'kanban.db'), { readOnly: true })

  try {
    return db
      .prepare('SELECT task_id, platform, chat_id FROM kanban_notify_subs ORDER BY created_at')
      .all() as unknown as NotifySub[]
  } catch {
    return []
  } finally {
    db.close()
  }
}

const composer = () => fixture!.page.locator('[data-slot="composer-root"] [contenteditable="true"]').filter({ visible: true }).first()
const transcript = () => fixture!.page.locator('[data-slot="aui_thread-viewport"]')
const assistantMessages = () => fixture!.page.locator('[data-slot="aui_assistant-message-content"]')

async function send(text: string): Promise<void> {
  const input = composer()
  await input.fill(text)
  await input.press('Enter')
}

test('a finished card created in chat wakes this Desktop session', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  const mock = fixture!.mock

  await send(KANBAN_CARD_TRIGGER)
  await expect(assistantMessages().filter({ hasText: KANBAN_CARD_CREATED_TEXT })).toHaveCount(1, { timeout: 120_000 })

  // `kanban_create` subscribed THIS session: local platform, the session's own id.
  await expect.poll(() => notifySubs().length, { timeout: 60_000 }).toBe(1)
  const sub = notifySubs()[0]
  expect(sub.platform).toBe('local')
  expect(sub.chat_id).toMatch(/^local-/)
  localSessionId = sub.chat_id
  const taskId = sub.task_id

  await page.screenshot({ path: test.info().outputPath('card-created.png') })

  const repliesBefore = await assistantMessages().count()
  const promptsBefore = mock.receivedPrompts.length

  hermesCli('kanban', 'complete', taskId, '--summary', 'E2E card done')

  // The notifier's wake is a normal user turn in this very chat...
  await expect(transcript()).toContainText(new RegExp(`\\[kanban\\] Task ${taskId} completed`), { timeout: 120_000 })
  // ...and the agent answered it.
  await expect.poll(() => assistantMessages().count(), { timeout: 120_000 }).toBeGreaterThan(repliesBefore)
  await expect(assistantMessages().filter({ hasText: MOCK_REPLY }).last()).toBeVisible()
  // The mock saw the wake prompt with the handoff summary, not a bare ping.
  const wakePrompts = mock.receivedPrompts.slice(promptsBefore)
  expect(wakePrompts.some(prompt => prompt.includes('E2E card done'))).toBe(true)
  await expect(transcript()).toContainText(KANBAN_CARD_TITLE)
  await page.screenshot({ path: test.info().outputPath('card-woke-session.png') })
})

test('a CLI subscription to the local session key wakes the same chat', async () => {
  test.setTimeout(240_000)
  const page = fixture!.page
  const mock = fixture!.mock
  const chatId = `agent:main:local:dm:${localSessionId}`

  const created = /Created (\S+)/.exec(hermesCli('kanban', 'create', 'E2E cli card', '--assignee', 'ghost'))
  expect(created).not.toBeNull()
  const taskId = created![1]
  hermesCli('kanban', 'notify-subscribe', taskId, '--platform', 'tui', '--chat-id', chatId)

  const repliesBefore = await assistantMessages().count()
  const promptsBefore = mock.receivedPrompts.length

  hermesCli('kanban', 'complete', taskId, '--summary', 'E2E cli card done')

  await expect(transcript()).toContainText(new RegExp(`\\[kanban\\] Task ${taskId} completed`), { timeout: 120_000 })
  await expect.poll(() => assistantMessages().count(), { timeout: 120_000 }).toBeGreaterThan(repliesBefore)
  expect(mock.receivedPrompts.slice(promptsBefore).some(prompt => prompt.includes('E2E cli card done'))).toBe(true)
  await page.screenshot({ path: test.info().outputPath('cli-card-woke-session.png') })
})
