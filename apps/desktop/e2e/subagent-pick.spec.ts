/**
 * The composer's Subagents pick, end to end on a Desktop local session: the
 * session runs on `gateway run` (the canonical owner), the pick is written
 * through `config.set key=delegation`, and the mock proves the real
 * `delegate_task` child's completion carries the picked model while the
 * parent's own completions stay on the default one. A reload reads the pick
 * back from `session.info`; Auto lets the next child fall back.
 *
 * Prerequisite: `npm run build` must have been run so dist/ exists.
 */

import { DELEGATE_GOAL, DELEGATE_REPLY, DELEGATE_TRIGGER, MOCK_REPLY } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const SUB_MODEL = 'mock-subagent-model'
const DEFAULT_MODEL = 'mock-model'
const OPENING_MESSAGE = 'E2E subagent pick: open a live session first'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({ mockServer: { extraModels: [SUB_MODEL] } })
  await waitForAppReady(fixture!, 120_000)
  // Keep the run out of the operator's way: the window stays "visible" to
  // Playwright and the renderer, but transparent and click-through on screen.
  await fixture!.app.evaluate(({ BrowserWindow }) => {
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

async function sendMessage(text: string): Promise<void> {
  const page = fixture!.page
  const composer = page.locator('[contenteditable="true"]:visible').first()

  await composer.click()
  await composer.type(text)
  await page.keyboard.press('Enter')
}

const pill = () => fixture!.page.locator('[data-testid="delegation-pill"]')

/** How many times the mock's delegate answer is in the transcript (one per delegated turn). */
async function replyCount(): Promise<number> {
  return fixture!.page.evaluate(
    text => (document.querySelector('[data-slot="aui_thread-viewport"]')?.textContent ?? '').split(text).length - 1,
    DELEGATE_REPLY
  )
}

/** Completions of the child itself; a delegation-complete notice mentions the goal mid-text. */
function childCalls(): number {
  return fixture!.mock.receivedPrompts.filter(prompt => prompt.trim().startsWith(DELEGATE_GOAL)).length
}

test('the Subagents pick routes the children of a Desktop local session and survives a reload', async () => {
  test.slow()

  const page = fixture!.page
  const mock = fixture!.mock

  // A draft has no runtime to write the pick to; one turn makes it a live session.
  await sendMessage(OPENING_MESSAGE)
  await expect(page.getByText(MOCK_REPLY).first()).toBeVisible({ timeout: 60_000 })
  await expect(pill()).toBeEnabled({ timeout: 30_000 })
  await expect(pill()).toContainText('Auto')

  // Pick through the pill's own catalog menu, like a user would.
  await pill().click()
  const search = page.getByRole('textbox', { name: 'Search models' })
  await search.fill('subagent')
  await expect(page.getByRole('menuitem').filter({ hasText: 'Subagent Model' })).toBeVisible({ timeout: 15_000 })
  await search.press('Enter')
  await expect(pill()).toContainText(SUB_MODEL, { timeout: 15_000 })
  await page.screenshot({ path: test.info().outputPath('pick.png') })

  // The forced route reaches the real child; the parent keeps its own model.
  const firstIndex = mock.receivedModels.length
  await sendMessage(DELEGATE_TRIGGER)
  await expect.poll(replyCount, { timeout: 120_000 }).toBe(1)
  // The child runs in the background, so its completion can land after the parent's reply.
  await expect
    .poll(() => mock.receivedModels.slice(firstIndex).filter(model => model === SUB_MODEL).length, { timeout: 60_000 })
    .toBe(1)
  const routed = mock.receivedModels.slice(firstIndex)
  expect(routed[0]).toBe(DEFAULT_MODEL)
  expect(routed.filter(model => model !== SUB_MODEL).every(model => model === DEFAULT_MODEL)).toBe(true)
  await page.screenshot({ path: test.info().outputPath('routed.png') })

  // A reload resumes the session; the pick comes back from `session.info`.
  await page.reload()
  await waitForAppReady(fixture!, 120_000)
  const row = page.locator('[data-sessions-mode] button').first()
  await row.waitFor({ state: 'visible', timeout: 60_000 })
  await row.click()
  await expect(pill()).toContainText(SUB_MODEL, { timeout: 60_000 })
  await page.screenshot({ path: test.info().outputPath('after-reload.png') })

  // Auto: the next child goes back to the default model.
  await pill().click()
  await page.getByTestId('delegation-auto').click()
  await expect(pill()).toContainText('Auto', { timeout: 15_000 })

  const repliesBefore = await replyCount()
  const childrenBefore = childCalls()
  const secondIndex = mock.receivedModels.length
  await sendMessage(DELEGATE_TRIGGER)
  await expect.poll(replyCount, { timeout: 120_000 }).toBe(repliesBefore + 1)
  await expect.poll(childCalls, { timeout: 60_000 }).toBe(childrenBefore + 1)
  const auto = mock.receivedModels.slice(secondIndex)
  expect(auto, `models after Auto: ${auto.join(', ')}`).not.toContain(SUB_MODEL)
  expect(auto[0]).toBe(DEFAULT_MODEL)
  await page.screenshot({ path: test.info().outputPath('auto.png') })
})
