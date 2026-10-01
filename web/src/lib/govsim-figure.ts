import type { ExplorationRow } from './exploration-data.ts';
import type { TopLevelSpec } from 'vega-lite';

/** Labels are facet headers, not a y axis. Stack them above each phone-sized
 * plot, keeping sample sizes on a separate line so ellipses cannot hide them. */
export function compactFigureSpec(spec: TopLevelSpec): TopLevelSpec {
  if (!('facet' in spec) || !('row' in spec.facet) || !spec.facet.row) return spec;
  const row = spec.facet.row;
  return { ...spec, facet: { ...spec.facet, row: { ...row, title: null,
    header: { ...row.header, title: null, labelOrient: 'top', labelAnchor: 'start', labelLimit: 130,
      // Split on the double space before `(n = …)` emitted by prepareFigure's model_label below.
      labelPadding: 8, labelExpr: "split(replace(replace(datum.label, /^[^/]+\\//, ''), ' · condition ', ' · #'), '  ')" },
  } } };
}

export const scenarios = { fish: 'Fishing', sheep: 'Sheep farming', pollution: 'Pollution' };
export const treatments = {
  baseline_concurrent: 'Baseline',
  baseline_concurrent_universalization: 'Universalization',
  perturbation_no_language: 'No discussion',
  perturbation_outsider: 'Outsider',
  perturbation_outsider_universalization: 'Outsider + universalization',
  baseline_concurrent_paraphrase_1: 'Paraphrase 1 (fish only)',
  baseline_concurrent_paraphrase_2: 'Paraphrase 2 (fish only)',
};
export const outcomes = {
  result_survival_months: { label: 'Resource survival', axis: 'Observed survival duration (months)' },
  result_final_resource: { label: 'Remaining resource', axis: 'Resource remaining after final harvest (scenario units)' },
  result_equality: { label: 'Harvest equality', axis: 'Harvest equality (1 − Gini coefficient)' },
  result_gain_per_agent: { label: 'Harvest per agent', axis: 'Mean cumulative harvest per agent (scenario units)' },
};
export type Metric = keyof typeof outcomes;
export type Filters = { scenario: string; treatment: string; rounds: string; embedder: string };
export function matchesSettings(row: ExplorationRow, filters: Filters): boolean {
  return (filters.rounds === 'all' || String(row.param_max_rounds) === filters.rounds)
    && (filters.embedder === 'all' || row.param_embedder === filters.embedder);
}
export function hasOutcome(row: ExplorationRow, metric: Metric): boolean {
  return row.state === 'completed' && typeof row[metric] === 'number' && Number.isFinite(row[metric]);
}
// Linear interpolation, equivalent to Vega's quartile summaries (R type 7).
export function quantile(sorted: number[], probability: number): number | null {
  if (!sorted.length) return null;
  const index = (sorted.length - 1) * probability;
  const lower = Math.floor(index);
  return sorted[lower]! + (sorted[Math.ceil(index)]! - sorted[lower]!) * (index - lower);
}
export function summarize(rows: ExplorationRow[], metric: Metric) {
  const groups = new Map<string, ExplorationRow[]>();
  for (const row of rows) {
    const key = JSON.stringify([row.param_model, row.condition]);
    groups.set(key, [...(groups.get(key) ?? []), row]);
  }
  return [...groups.values()].map(group => {
    const valid = group.filter(row => hasOutcome(row, metric));
    const values = valid.map(row => row[metric] as number).sort((a, b) => a - b);
    return {
      model: String(group[0]!.param_model ?? 'Unknown model'),
      condition: String(group[0]!.condition),
      n: valid.length, total: group.length,
      seeds: new Set(valid.map(row => row.seed).filter(seed => seed !== null && seed !== undefined)).size,
      missing: group.filter(row => row.state === 'completed' && !hasOutcome(row, metric)).length,
      incomplete: group.filter(row => row.state !== 'completed').length,
      minimum: values[0] ?? null, maximum: values.at(-1) ?? null,
      median: quantile(values, .5), q1: quantile(values, .25), q3: quantile(values, .75),
    };
  }).sort((a, b) => a.model.localeCompare(b.model) || a.condition.localeCompare(b.condition));
}
export function prepareFigure(rows: ExplorationRow[], metric: Metric) {
  const groups = summarize(rows, metric);
  const points = groups.flatMap((group, groupIndex) => {
    const valid = rows.filter(row => row.param_model === group.model && row.condition === group.condition && hasOutcome(row, metric))
      .sort((a, b) => String(a.run).localeCompare(String(b.run)));
    // Spread tied observations so every run is visible, with no random jitter.
    return valid.map((row, index) => {
      const ties = valid.filter(other => other[metric] === row[metric]);
      const tieIndex = ties.findIndex(other => other.run === row.run);
      const multipleConditions = groups.filter(other => other.model === group.model).length > 1;
      return { ...row, outcome: row[metric], samples: group.n, minimum: group.minimum, maximum: group.maximum,
        median: group.median, q1: group.q1, q3: group.q3,
        group: groupIndex,
        model_label: `${group.model}  (n = ${group.n})${multipleConditions ? ` · condition ${groupIndex + 1} (${group.condition.slice(0, 8)})` : ''}`,
        jitter: ties.length > 1 ? -.25 + .5 * tieIndex / (ties.length - 1) : 0,
        point_index: index,
      };
    });
  });
  return { groups, points };
}
