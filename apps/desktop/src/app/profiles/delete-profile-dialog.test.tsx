import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type * as HermesModule from '@/hermes'
import { I18nProvider } from '@/i18n'
import { setSpawnTaskChoice, spawnTaskChoiceFor, spawnTaskChoiceScope } from '@/store/spawn-task'

import { DeleteProfileDialog } from './delete-profile-dialog'

const deleteProfile = vi.fn(async (..._args: unknown[]) => undefined)

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof HermesModule>()),
  deleteProfile: (...args: unknown[]) => deleteProfile(...args)
}))
vi.mock('@/store/gateway', () => ({ retireLocalProfileGateways: vi.fn() }))

afterEach(() => {
  cleanup()
  window.localStorage.clear()
})

const CHOICE = { effort: '', fast: false, mode: 'tab' as const, model: 'm', pin: false, provider: 'p' }

describe('DeleteProfileDialog', () => {
  // A remote profile's spawn-task pick is keyed by its connection; deleting
  // that profile must forget it. (The route-less tile drop the dialog runs
  // also clears a same-named LOCAL profile's state — every profile-keyed
  // family behaves that way today; not asserted here.)
  it('forgets the deleted remote profile’s remembered spawn-task choice', async () => {
    setSpawnTaskChoice(spawnTaskChoiceScope('homelab', 'work'), CHOICE)
    setSpawnTaskChoice(spawnTaskChoiceScope('homelab', 'other'), CHOICE)

    render(
      <I18nProvider configClient={null} initialLocale="en">
        <DeleteProfileDialog
          onClose={() => undefined}
          open
          profile={{ name: 'work', path: '/remote/work' }}
          scope={{ connectionId: 'homelab', profile: 'work' }}
        />
      </I18nProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(spawnTaskChoiceFor(spawnTaskChoiceScope('homelab', 'work')).model).toBe(''))
    expect(spawnTaskChoiceFor(spawnTaskChoiceScope('homelab', 'other')).model).toBe('m')
  })
})
