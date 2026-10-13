import { afterEach, describe, expect, it, vi } from 'vitest'

// The window kind is read once from location.search and cached, so each case
// loads a fresh module under its own URL.
async function canHostTilesAt(search: string): Promise<boolean> {
  window.history.replaceState(null, '', `/${search}`)
  vi.resetModules()

  return (await import('./windows')).canHostSessionTiles()
}

afterEach(() => window.history.replaceState(null, '', '/'))

describe('canHostSessionTiles', () => {
  // A spawn-task chip launches into a tile; a window that never mounts tiles
  // must not register a launcher (the task would never be sent).
  it('is false for the HUD and a popped-out browser, true for the main and secondary windows', async () => {
    expect(await canHostTilesAt('?win=hud')).toBe(false)
    expect(await canHostTilesAt('?win=browser')).toBe(false)
    expect(await canHostTilesAt('')).toBe(true)
    expect(await canHostTilesAt('?win=secondary')).toBe(true)
  })
})
