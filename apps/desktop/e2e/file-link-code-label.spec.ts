/**
 * A model cites a file as [`tmp/x/proposed.md`](tmp/x/proposed.md): the
 * backticked label used to cut the link in two and it painted "[blocked]".
 * The link must render as a file card whose Open preview shows the file.
 */
import fs from 'node:fs'
import path from 'node:path'

import { MOCK_REPLY } from '../../../tests-js/scripts/mock-server'

import { type MockBackendFixture, setupMockBackend, waitForAppReady } from './fixtures'
import { expect, test } from './test'

const TRIGGER = 'E2E_FILE_LINK_CODE_LABEL'
const REL = 'tmp/e2e-file-link/proposed.md'
const BODY = 'E2E proposed issue body'
const REPLY = `Превью готово.\n\n- Превью: [\`${REL}\`](${REL})\n`

let fixture: MockBackendFixture | null = null

test.beforeAll(async () => {
  fixture = await setupMockBackend({
    mockServer: { replyForPrompt: text => (text.includes(TRIGGER) ? REPLY : MOCK_REPLY) }
  })
  // HOME is the sandbox root; the card falls back to HOME when the cwd misses.
  const file = path.join(fixture.sandbox.root, REL)
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(file, `# Proposed\n\n${BODY}\n`)
  await waitForAppReady(fixture, 120_000)
  await fixture.app.evaluate(({ BrowserWindow }) => {
    for (const win of BrowserWindow.getAllWindows()) {
      win.setOpacity(0)
      win.setIgnoreMouseEvents(true)
    }
  })
})

test.afterAll(async () => {
  await fixture?.cleanup()
  fixture = null
})

test('a file link with an inline-code label opens through the preview card', async () => {
  const page = fixture!.page
  const composer = page.locator('[contenteditable="true"]').first()

  await composer.click()
  await composer.type(TRIGGER)
  await page.keyboard.press('Enter')

  const reply = page.locator('[data-slot="aui_assistant-message-root"]').filter({ hasText: 'Превью готово' })
  await expect(reply).toBeVisible({ timeout: 60_000 })
  await expect(reply.getByText('proposed.md')).toBeVisible()
  await expect(reply.getByText(/blocked/)).toHaveCount(0)

  await reply.getByRole('button', { name: 'Open preview' }).click()
  await expect(page.getByText(BODY)).toBeVisible({ timeout: 30_000 })
  await page.screenshot({ path: test.info().outputPath('file-link-code-label.png') })
})
