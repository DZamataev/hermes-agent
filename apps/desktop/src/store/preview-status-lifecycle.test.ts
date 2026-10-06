import { afterEach, expect, it, vi } from 'vitest'

import {
  $previewStatusBySession,
  clearPreviewArtifacts,
  dismissPreviewArtifact,
  recordPreviewArtifact
} from './preview-status'
import { dropTilesForProfile, migrateTilesForProfile, recordSessionEventScope } from './session-states'
import { setSpawnTaskChoice, spawnTaskChoiceFor, spawnTaskChoiceScope } from './spawn-task'

afterEach(() => {
  vi.restoreAllMocks()
  $previewStatusBySession.set({})
  window.localStorage.clear()
})

it('moves local dismissals on rename and removes only the deleted owner', () => {
  for (const [runtime, connectionId] of [
    ['rename-local', 'local'],
    ['rename-remote', 'remote']
  ]) {
    recordSessionEventScope({ session_id: runtime, connectionId, profile: 'before-rename' })
    recordPreviewArtifact(runtime, '/work/rename.html', '/work', 'rename-stored')
    dismissPreviewArtifact(runtime, '/work/rename.html', 'rename-stored')
  }

  migrateTilesForProfile('before-rename', 'after-rename')
  recordSessionEventScope({ session_id: 'rename-new', connectionId: 'local', profile: 'after-rename' })
  recordPreviewArtifact('rename-new', '/work/rename.html', '/work', 'rename-stored')
  recordPreviewArtifact('rename-remote', '/work/rename.html', '/work', 'rename-stored')
  expect($previewStatusBySession.get()['rename-new']).toBeUndefined()
  expect($previewStatusBySession.get()['rename-remote']).toBeUndefined()
  dropTilesForProfile('after-rename')
  recordPreviewArtifact('rename-new', '/work/rename.html', '/work', 'rename-stored')
  recordPreviewArtifact('rename-remote', '/work/rename.html', '/work', 'rename-stored')
  expect($previewStatusBySession.get()['rename-new']).toHaveLength(1)
  expect($previewStatusBySession.get()['rename-remote']).toBeUndefined()
})

it('keeps a close effective in memory when storage writes fail', () => {
  recordSessionEventScope({ session_id: 'quota', connectionId: 'local', profile: 'quota' })
  recordPreviewArtifact('quota', '/work/quota.html', '/work', 'quota-stored')

  const write = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota')
  })

  dismissPreviewArtifact('quota', '/work/quota.html', 'quota-stored')
  clearPreviewArtifacts('quota')
  recordPreviewArtifact('quota', '/work/quota.html', '/work', 'quota-stored')
  expect($previewStatusBySession.get().quota).toBeUndefined()
  write.mockRestore()
})

it('honors a durable close after module reload', async () => {
  recordSessionEventScope({ session_id: 'before-reload', connectionId: 'local', profile: 'persist' })
  recordPreviewArtifact('before-reload', '/work/saved.html', '/work', 'persist-stored')
  dismissPreviewArtifact('before-reload', '/work/saved.html', 'persist-stored')
  vi.resetModules()
  const fresh = await import('./preview-status')
  const { recordSessionEventScope: scope } = await import('./session-states')
  scope({ session_id: 'after-reload', connectionId: 'local', profile: 'persist' })
  fresh.recordPreviewArtifact('after-reload', '/work/saved.html', '/work', 'persist-stored')
  expect(fresh.$previewStatusBySession.get()['after-reload']).toBeUndefined()
})

// The spawn-task chip's remembered pick is the same kind of profile-keyed
// family: the rename and delete entry points must carry it along.
it('moves the spawn-task chip choice on rename and forgets it on delete', () => {
  const choice = { effort: 'high', fast: false, mode: 'tab' as const, model: 'm', pin: false, provider: 'p' }

  setSpawnTaskChoice(spawnTaskChoiceScope('local', 'before-rename'), choice)

  migrateTilesForProfile('before-rename', 'after-rename')
  expect(spawnTaskChoiceFor(spawnTaskChoiceScope('local', 'after-rename')).model).toBe('m')
  expect(spawnTaskChoiceFor(spawnTaskChoiceScope('local', 'before-rename')).model).toBe('')

  dropTilesForProfile('after-rename')
  expect(spawnTaskChoiceFor(spawnTaskChoiceScope('local', 'after-rename')).model).toBe('')
})

// The chip keys a routed owner by its backend profile; an SDK delete that
// names both must forget the choice under that key too.
it('forgets a routed owner’s spawn-task choice on a source-scoped delete', () => {
  const choice = { effort: '', fast: false, mode: 'tab' as const, model: 'm', pin: false, provider: 'p' }

  setSpawnTaskChoice(spawnTaskChoiceScope('homelab', 'backend-name'), choice)
  dropTilesForProfile('desktop-name', { connectionId: 'homelab', profile: 'desktop-name', targetProfile: 'backend-name' })

  expect(spawnTaskChoiceFor(spawnTaskChoiceScope('homelab', 'backend-name')).model).toBe('')
})
