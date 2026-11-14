export interface SearchablePickerOption {
  label: string;
  sub?: string;
  keywords?: string;
  group?: string;
}

/** Match native symbols without changing their displayed or selected identity. */
export function searchPickerGroups<T extends SearchablePickerOption>(options: readonly T[], query: string): { label: string; options: T[]; start: number }[] {
  const compact = query.toLowerCase().replace(/[\s/_:.-]+/g, '');
  const terms = query.toLowerCase().split(/\s+/).map(term => term.replace(/[/_:.-]+/g, '')).filter(Boolean);
  const ranked = options.flatMap((option, index) => {
    const label = option.label.toLowerCase().replace(/[\s/_:.-]+/g, '');
    const fields = [label, option.sub ?? '', option.keywords ?? '', option.group ?? ''].map(field => field.toLowerCase().replace(/[\s/_:.-]+/g, ''));
    if (compact && !fields.some(field => field.includes(compact)) && !terms.every(term => fields.some(field => field.includes(term)))) return [];
    const rank = compact && label === compact ? 0 : compact && label.startsWith(compact) ? 1 : 2;
    return [{ option, rank, index }];
  }).sort((a, b) => a.rank - b.rank || a.index - b.index);
  const groups = new Map<string, { label: string; options: T[]; start: number }>();
  for (const { option } of ranked) {
    const label = option.group ?? '';
    let group = groups.get(label);
    if (!group) { group = { label, options: [], start: 0 }; groups.set(label, group); }
    group.options.push(option);
  }
  let start = 0;
  for (const group of groups.values()) { group.start = start; start += group.options.length; }
  return [...groups.values()];
}
