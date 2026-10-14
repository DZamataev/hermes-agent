import { describe, expect, it } from 'vitest'

import { sessionCreateOverrideParams } from './create-overrides'

describe('session.create override params', () => {
  it('names the session a spawn-task result goes back to', () => {
    expect(sessionCreateOverrideParams({ spawnReportTo: 'parent-stored' })).toMatchObject({
      spawn_report_to: 'parent-stored'
    })
    expect(sessionCreateOverrideParams({ title: 'T' })).not.toHaveProperty('spawn_report_to')
  })

  // A blank id is not an address: the backend's `_str_param(...) or None` keeps `'  '` as a
  // truthy address, and no poller would ever resolve it.
  it('trims the address, and ships nothing for a blank one', () => {
    expect(sessionCreateOverrideParams({ spawnReportTo: ' parent-stored ' })).toMatchObject({
      spawn_report_to: 'parent-stored'
    })
    expect(sessionCreateOverrideParams({ spawnReportTo: '' })).not.toHaveProperty('spawn_report_to')
    expect(sessionCreateOverrideParams({ spawnReportTo: '   ' })).not.toHaveProperty('spawn_report_to')
  })
})
