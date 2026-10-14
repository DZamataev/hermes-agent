import { describe, expect, it } from 'vitest'

import { sessionCreateOverrideParams } from './create-overrides'

describe('session.create override params', () => {
  it('names the session a spawn-task result goes back to', () => {
    expect(sessionCreateOverrideParams({ spawnReportTo: 'parent-stored' })).toMatchObject({
      spawn_report_to: 'parent-stored'
    })
    expect(sessionCreateOverrideParams({ title: 'T' })).not.toHaveProperty('spawn_report_to')
  })
})
