/**
 * The composer ghost on a Desktop local session, end to end.
 *
 * A local session's turns run on the canonical gateway owner (`gateway run`), not in the sidecar
 * whose turn loop computes the suggestion: without the owner's `complete.suggest` wiring a
 * completed turn leaves the composer without a ghost. The mock recognises the prediction request
 * by its prompt marker and answers E2E_PREDICTED_NEXT; the spec drives the composer like a user —
 * two exchanges (a prediction needs a reply to continue), the ghost in the empty composer, Tab.
 *
 * Prerequisite: `npm run build` must have been run so dist/ exists.
 */

import { E2E_PREDICTED_NEXT, MOCK_REPLY } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({
    // The keys the gateway (`composer_suggestions`) and Desktop (`composer_predictions`) read.
    extraDisplayConfig: 'composer_predictions: true\ncomposer_suggestions: true',
  })
  await waitForAppReady(fixture, 120_000)
  // Out of the operator's way: visible to Playwright, transparent and click-through on screen.
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

test('a desktop local session shows the predicted next message as the composer ghost', async () => {
  test.setTimeout(180_000)
  const page = fixture!.page
  const composer = page.locator('[data-slot="composer-root"] [contenteditable="true"]').filter({ visible: true }).first()
  const replies = page.locator('[data-slot="aui_assistant-message-content"]').filter({ hasText: MOCK_REPLY })

  // Two turns: the first has no conversation to predict from, so only the second bills a prediction.
  for (const [index, text] of ['First message for the ghost', 'Second message for the ghost'].entries()) {
    await composer.fill(text)
    await composer.press('Enter')
    await expect(replies).toHaveCount(index + 1, { timeout: 60_000 })
  }

  // The ghost rides the empty composer's placeholder, read back from the turn's `complete.suggest`.
  // Clear first: a stray keystroke on the operator's Mac lands in the focused window and hides it.
  await composer.fill('')
  await expect(composer).toHaveAttribute('data-placeholder', E2E_PREDICTED_NEXT, { timeout: 90_000 })
  await page.screenshot({ path: test.info().outputPath('composer-ghost.png') })

  await composer.click()
  await composer.press('Tab')
  await expect(composer).toHaveText(E2E_PREDICTED_NEXT)
})
