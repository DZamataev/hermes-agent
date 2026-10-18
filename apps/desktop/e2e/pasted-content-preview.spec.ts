/**
 * A large paste is sent as a `@file:` attachment and the bubble shows only a
 * "Pasted content" chip, so the chip is the one way back to the text. This
 * drives the real chain: paste into the composer, the Electron writer, send
 * through the gateway, then open the chip from the sent bubble, live and again
 * after the transcript is rebuilt from the session history.
 */

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, type Page, test } from './test'

const MARKER = 'E2E-PASTE-MARKER-7f3a'
const PASTE = `${MARKER}\n${'pasted line of source material\n'.repeat(160)}`

let fixture: MockBackendFixture | null = null

/** Starts from a closed preview tab, since the rail restores its tabs across a
 *  reload. The marker may also reach a title, so it is counted, not found. */
async function expectChipOpensPaste(page: Page, chip: ReturnType<Page['locator']>) {
  const tab = page.getByRole('tab', { name: /pasted_content_[\w-]+\.txt/ })

  while ((await tab.count()) > 0) {
    // The close button shows on hover.
    await tab.first().hover()
    await tab.first().getByRole('button', { name: 'Close' }).click()
  }

  const before = await page.getByText(MARKER).count()

  await chip.click()

  await expect(tab).toHaveCount(1, { timeout: 15_000 })
  await expect.poll(() => page.getByText(MARKER).count(), { timeout: 15_000 }).toBeGreaterThan(before)
}

test.beforeAll(async () => {
  fixture = await setupMockBackend()
  await waitForAppReady(fixture!, 120_000)
  // Keep a local run off the operator's screen; Playwright still drives it.
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

test('a sent large paste opens in the preview rail from its chip', async () => {
  const page = fixture!.page
  const composer = page.locator('[contenteditable="true"]').first()

  await composer.click()
  await composer.evaluate((editor, text) => {
    const data = new DataTransfer()
    data.setData('text/plain', text)
    editor.dispatchEvent(new ClipboardEvent('paste', { bubbles: true, cancelable: true, clipboardData: data }))
  }, PASTE)

  await expect(page.locator('[data-slot="composer-attachments"]').getByText(/Pasted content/)).toBeVisible()
  await composer.click()
  await page.keyboard.press('Enter')

  const bubbleChip = page.locator('[data-slot="aui_user-message-root"] [data-slot="aui_directive-chip"]')
  await expect(bubbleChip).toHaveText('Pasted content', { timeout: 30_000 })
  await expectChipOpensPaste(page, bubbleChip)
  await expect(page.getByRole('textbox', { name: 'Edit message' })).toHaveCount(0)
  await page.screenshot({ path: test.info().outputPath('paste-preview-live.png') })
})

test('the chip still opens the paste once the transcript is rebuilt from history', async () => {
  const page = fixture!.page

  await page.reload()
  await waitForAppReady(fixture!, 120_000)

  const bubbleChip = page.locator('[data-slot="aui_user-message-root"] [data-slot="aui_directive-chip"]')

  // The reload lands on a draft; the sent chat is the only row in the sidebar,
  // previewed by its first line, the paste's ref.
  await page
    .getByRole('button', { name: /^@file:.*pasted_content_/ })
    .first()
    .click()

  await expect(bubbleChip).toHaveText('Pasted content', { timeout: 30_000 })
  await expectChipOpensPaste(page, bubbleChip)
  await page.screenshot({ path: test.info().outputPath('paste-preview-history.png') })
})
