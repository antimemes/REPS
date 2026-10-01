import { useContext, useState } from 'react';
import type { TopLevelSpec } from 'vega-lite';
import { RunDataContext, VegaChart } from './vega-chart';
import { compactFigureSpec, hasOutcome, matchesSettings, outcomes, prepareFigure, scenarios, treatments, type Filters, type Metric } from '../lib/govsim-figure';
import { useMediaQuery } from '../lib/use-media-query';

const fmt = (value: number | null) => value === null ? '—' : value.toLocaleString('en-US', { maximumFractionDigits: 3 });

export function GovSimFigure({ spec }: { spec: TopLevelSpec }) {
  const rows = useContext(RunDataContext);
  const [scenario, setScenario] = useState('fish');
  const [treatment, setTreatment] = useState('baseline_concurrent');
  const [metric, setMetric] = useState<Metric>('result_survival_months');
  const phone = useMediaQuery('(max-width: 767px)');
  if (rows === null) return <p>Loading run data…</p>;
  const filters: Filters = { scenario, treatment, rounds: '0', embedder: 'all' };
  const base = rows.filter(row => matchesSettings(row, filters));
  const selected = base.filter(row => row.param_experiment === `${scenario}_${treatment}`);
  const { groups, points } = prepareFigure(selected, metric);
  const total = groups.reduce((sum, group) => sum + group.n, 0);
  const excluded = selected.length - total;
  const models = [...new Set(base.map(row => String(row.param_model)))].sort();
  const embedders = [...new Set(rows.map(row => row.param_embedder).filter(value => typeof value === 'string'))].sort();
  const coverage = Object.entries(scenarios).flatMap(([key, name]) => Object.entries(treatments).flatMap(([variant, label]) => {
    const conditionRows = base.filter(row => row.param_experiment === `${key}_${variant}`);
    return conditionRows.length ? [{ scenario: key, treatment: variant, label: `${name} · ${label}`, rows: conditionRows }] : [];
  }));
  const scenarioLabel = scenarios[scenario as keyof typeof scenarios];
  const treatmentLabel = treatments[treatment as keyof typeof treatments];
  // The figure's exported SVG carries the selection and sample-size context.
  const chart: TopLevelSpec = {
    ...(phone ? compactFigureSpec(spec) : spec),
    title: {
      text: `${scenarioLabel} · ${treatmentLabel}`,
      subtitle: phone ? [
        `${outcomes[metric].label} · ${total} completed runs`,
        `${groups.length} model–condition groups · full scenario`,
        `Embedder: ${embedders.join(', ') || 'none recorded'}`,
        'Dots: runs · Diamond: median · Bar: IQR',
        'Shading: normalized KDE (n ≥ 5)',
      ] : [
        `${outcomes[metric].label} | ${total} completed runs | ${groups.length} model–condition groups`,
        `Length: full scenario · Embedder: ${embedders.join(', ') || 'none recorded'}`,
        'Dots: runs · Diamond: median · Bar: IQR · Shading: normalized KDE (n ≥ 5)',
      ],
      anchor: 'start', fontSize: 16, subtitleFontSize: phone ? 10 : 12, subtitleLineHeight: phone ? 15 : 18, offset: 18,
    },
    params: [
      { name: 'outcomeAxis', value: phone ? outcomes[metric].axis.split(/ (?=\()/) : outcomes[metric].axis },
      { name: 'modelDomain', value: [...new Set(rows.map(row => String(row.param_model)))].sort() },
    ],
  };
  return <section className="govsim-results" aria-label="GovSim statistical comparison">
    <div className="govsim-controls">
      <label>Scenario<select aria-label="Scenario" value={scenario} onChange={event => setScenario(event.target.value)}>{Object.entries(scenarios).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></label>
      <label>Treatment<select aria-label="Treatment" value={treatment} onChange={event => setTreatment(event.target.value)}>{[
        { label: 'Main treatments', keys: ['baseline_concurrent', 'baseline_concurrent_universalization', 'perturbation_no_language', 'perturbation_outsider'] },
        { label: 'Not reported in the paper', keys: ['perturbation_outsider_universalization'] },
      ].map(group => <optgroup key={group.label} label={group.label}>{group.keys.map(key => <option key={key} value={key}>{treatments[key as keyof typeof treatments]}</option>)}</optgroup>)}</select></label>
      <label>Outcome<select aria-label="Outcome" value={metric} onChange={event => setMetric(event.target.value as Metric)}>{Object.entries(outcomes).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</select></label>
    </div>
    {treatment.includes('paraphrase') && <p className="govsim-caption">Optional upstream variant, fishing only: alternative wording of the baseline instructions. Not listed in the authors’ <a href="https://github.com/giorgiopiatti/GovSim#table-of-experiments">table of paper experiments</a>.</p>}
    <p className="govsim-sample-note" role="status"><strong>{total} plotted runs</strong> across {groups.length} model–condition groups. {excluded} excluded ({groups.reduce((n, g) => n + g.incomplete, 0)} not completed; {groups.reduce((n, g) => n + g.missing, 0)} missing outcome). Each dot is one simulation run.</p>
    {points.length ? <RunDataContext value={points}><VegaChart spec={chart} horizontalPadding={phone ? 170 : 330} minPlotWidth={phone ? 220 : 420} /></RunDataContext> : <p className="govsim-empty">No completed runs with this outcome match the selection. Choose an available condition in the sample-size table below.</p>}
    <p className="govsim-caption"><strong>Dots:</strong> individual runs, colored by model. <strong>Diamond:</strong> median. <strong>Bar:</strong> middle 50% of outcomes (IQR), not a confidence interval. <strong>n:</strong> plotted runs.</p>
    {metric === 'result_survival_months' && <p className="govsim-caption">Survival at the time limit is right-censored: the resource might have survived longer.<span className="hover-guidance"> Hover over a run to check whether it collapsed.</span></p>}
    <details className="govsim-methods"><summary>Statistical methods</summary>
      <p className="govsim-caption">Violins use Gaussian kernel density estimation with Scott’s bandwidth, restricted to the observed range. They appear only for groups with at least 5 runs and nonzero variation. Width is normalized per group and does not represent sample size.</p>
      <p className="govsim-caption">Dots with identical outcomes are separated vertically for visibility. Quartiles use linear interpolation. Distinct recorded conditions are never pooled into one violin; colors remain consistent across selections. Repeated seeds do not necessarily represent independent replicates.</p>
    </details>
    <details className="govsim-methods govsim-statistics">
    <summary>Sample sizes and summary statistics</summary>
    <h3>Selected condition</h3>
    <div className="govsim-table-scroll" tabIndex={0} role="region" aria-label="Selected condition statistics"><table>
      <caption>{scenarioLabel} · {treatmentLabel} — {outcomes[metric].label}. Quartiles use linear interpolation. “Seeds” counts distinct recorded seeds among plotted runs.</caption>
      <thead><tr><th scope="col">Model / recorded condition</th><th scope="col">n / total</th><th scope="col">Seeds</th><th scope="col">Median</th><th scope="col">Q1–Q3</th><th scope="col">Range</th></tr></thead>
      <tbody>{groups.map(group => <tr key={group.model + group.condition}><th scope="row">{group.model}<a href={`#/conditions/${encodeURIComponent(group.condition)}`} title={group.condition}>Condition {group.condition.slice(0, 8)}</a></th><td>{group.n} / {group.total}</td><td>{group.seeds}</td><td>{fmt(group.median)}</td><td>{fmt(group.q1)}–{fmt(group.q3)}</td><td>{fmt(group.minimum)}–{fmt(group.maximum)}</td></tr>)}</tbody>
    </table></div>
    <h3>Sample sizes across conditions</h3>
    <p className="govsim-caption"><strong>Plotted / total runs</strong> for full-scenario runs under the current outcome. A dash means no runs. Select a row to compare it; distinct recorded conditions stay separate in the figure.</p>
    <div className="govsim-table-scroll" tabIndex={0} role="region" aria-label="Sample sizes across scenarios and treatments"><table>
      <caption>Completed runs with a valid outcome / all recorded runs, by scenario, treatment, and model.</caption>
      <thead><tr><th scope="col">Scenario · treatment</th>{models.map(model => <th scope="col" key={model}>{model}</th>)}<th scope="col">Total</th></tr></thead>
      <tbody>{coverage.map(entry => <tr key={entry.label} className={entry.scenario === scenario && entry.treatment === treatment ? 'selected-condition' : ''}><th scope="row"><button aria-pressed={entry.scenario === scenario && entry.treatment === treatment} onClick={() => { setScenario(entry.scenario); setTreatment(entry.treatment); }}>{entry.label}</button></th>{models.map(model => { const subset = entry.rows.filter(row => row.param_model === model); return <td key={model}>{subset.length ? `${subset.filter(row => hasOutcome(row, metric)).length} / ${subset.length}` : '—'}</td>; })}<td>{entry.rows.filter(row => hasOutcome(row, metric)).length} / {entry.rows.length}</td></tr>)}</tbody>
    </table></div>
    </details>
  </section>;
}
