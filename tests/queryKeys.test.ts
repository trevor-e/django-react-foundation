import { describe, expect, it } from 'vitest'
import { createQueryKeyFactory } from '../src/queryKeys'

describe('createQueryKeyFactory', () => {
  const keys = createQueryKeyFactory('widgets')

  it('builds the expected hierarchical keys', () => {
    expect(keys.all).toEqual(['widgets'])
    expect(keys.lists()).toEqual(['widgets', 'list'])
    expect(keys.list({ activeOnly: true })).toEqual(['widgets', 'list', { activeOnly: true }])
    expect(keys.details()).toEqual(['widgets', 'detail'])
    expect(keys.detail('42')).toEqual(['widgets', 'detail', '42'])
  })
})
