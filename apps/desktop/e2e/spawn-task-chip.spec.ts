/**
 * Spawn-task chip, end to end against a real `hermes serve` + mock inference.
 *
 * The mock offers a side task through the real `spawn_task` tool; the chip it
 * renders is driven like a user would: pick a model, start the task in a new
 * tab. The proof is on the inference side — the new session's first completion
 * carries the task prompt AND asks for the model picked on the chip — and the
 * next chip opens on that same model (the remembered choice).
 *
 * Prerequisite: `npm run build` must have been run so dist/ exists.
 */

import { SPAWN_TASK_PROMPT, SPAWN_TASK_TITLE, SPAWN_TASK_TRIGGER } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const SECOND_MODEL = 'mock-model-alt'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({ mockServer: { extraModels: [SECOND_MODEL] } })
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

async function offerTask(text: string) {
  const page = fixture!.page
  const composer = page.locator('[contenteditable="true"]:visible').first()

  await composer.click()
  await composer.type(text)
  await page.keyboard.press('Enter')
}

test('a spawned task runs as its own session on the model picked on the chip, and the pick is remembered', async () => {
  const page = fixture!.page
  const mock = fixture!.mock

  await offerTask(`${SPAWN_TASK_TRIGGER} first`)

  const chip = page.locator('[data-spawn-task]').first()
  await expect(chip.getByText(SPAWN_TASK_TITLE)).toBeVisible({ timeout: 30_000 })
  await page.screenshot({ path: test.info().outputPath('chip-offered.png') })

  // Pick the second model through the chip's own catalog menu.
  await chip.getByRole('button', { name: 'Model for the new session' }).click()
  const search = page.getByRole('textbox', { name: 'Search models' })
  await search.fill('alt')
  await expect(page.getByRole('menu')).toContainText('Mock Model Alt', { timeout: 15_000 })
  await search.press('Enter')
  await expect(chip.getByRole('button', { name: 'Model for the new session' })).toContainText(/alt/i)

  const completionsBefore = mock.receivedModels.length
  await chip.getByRole('button', { name: 'Start in new tab' }).click()

  // The new tab takes the foreground, so the offering chat (and its chip) is
  // now a background tab: assert the outcome, not its visibility.
  await expect(chip.getByText('Started in a new session')).toBeAttached({ timeout: 30_000 })

  // The launched session sent the task prompt as its first turn, on the chip's model.
  await expect
    .poll(() => mock.receivedPrompts.some(prompt => prompt.includes(SPAWN_TASK_PROMPT)), { timeout: 60_000 })
    .toBe(true)
  const launchedModels = mock.receivedModels.slice(completionsBefore)
  expect(launchedModels).toContain(SECOND_MODEL)
  // The new session carries the chip's title, so the tab and sidebar row name the task.
  await expect(page.getByRole('tab', { name: new RegExp(SPAWN_TASK_TITLE) })).toBeVisible({ timeout: 30_000 })
  await page.screenshot({ path: test.info().outputPath('chip-launched.png') })

  // A later chip — here in the freshly opened tab, which now holds focus —
  // opens on the same model: the choice was remembered. Two chips with the
  // same title exist now; wait for the one in the visible pane.
  await offerTask(`${SPAWN_TASK_TRIGGER} second`)
  const secondChip = page.locator('[data-spawn-task]:visible').filter({ hasText: SPAWN_TASK_TITLE }).last()
  await expect(secondChip.getByRole('button', { name: 'Model for the new session' })).toContainText(/alt/i, {
    timeout: 30_000
  })
  await page.screenshot({ path: test.info().outputPath('chip-remembered.png') })
})
