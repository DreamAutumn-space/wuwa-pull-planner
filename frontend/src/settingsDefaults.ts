export const DEFAULT_HEALERS = ['守岸人', '维里奈', '莫宁', '卜灵', '白芷', '穗穗']
const PREVIOUS_DEFAULT_HEALERS = DEFAULT_HEALERS.slice(0, -1)

/** Promote an old default without changing custom or explicitly empty lists. */
export function migrateRepeatableHealers(saved: string[] | undefined, promotePreviousDefault: boolean): string[] {
  if (saved === undefined) return [...DEFAULT_HEALERS]
  if (promotePreviousDefault
    && saved.length === PREVIOUS_DEFAULT_HEALERS.length
    && PREVIOUS_DEFAULT_HEALERS.every((name) => saved.includes(name))) {
    return [...DEFAULT_HEALERS]
  }
  return [...saved]
}
