/** A hierarchical, typed TanStack Query key factory for one resource.
 *
 * ```ts
 * const widgetKeys = createQueryKeyFactory('widgets')
 * useQuery({ queryKey: widgetKeys.detail('42'), queryFn: () => getWidget('42') })
 * queryClient.invalidateQueries({ queryKey: widgetKeys.lists() })
 * ```
 */
export function createQueryKeyFactory<Prefix extends string>(prefix: Prefix) {
  return {
    all: [prefix] as const,
    lists: () => [prefix, 'list'] as const,
    list: (filters: unknown) => [prefix, 'list', filters] as const,
    details: () => [prefix, 'detail'] as const,
    detail: (id: string | number) => [prefix, 'detail', id] as const,
  }
}
