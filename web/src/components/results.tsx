/* Result rows for run and experiment detail pages; compact chips for listings
   and matrix aggregates. Booleans stay neutral; structured values open a modal. */

import { useState } from "react";
import type { RunMeta, ResultDecl } from "@/shared/types";
import { ParamChip } from "@/components/param-value";
import { Segmented } from "@/components/bits";
import { resultDeclarations } from "@/lib/run-readability";
import { cn } from "@/lib/utils";

const BASE =
  "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 font-mono text-[11px] leading-5";
const NEUTRAL = "bg-muted/50";

export const fmtNum = (v: number): string =>
  Number.isInteger(v) ? String(v) : String(+v.toFixed(3));

/* Declaration order stays stable as live values arrive. */
export function RunResults({ definitions, summary }: {
  definitions: ResultDecl[]; summary: Record<string, unknown>;
}) {
  const [view, setView] = useState("values");
  return <div className="space-y-3">
    <div aria-label="Result presentation"><Segmented options={["values", "definitions"]} value={view} onChange={setView} /></div>
    {view === "definitions"
      ? <ResultRows definitions={definitions} summary={summary} showPending inlineDetails />
      : <ResultFacts definitions={definitions} summary={summary} />}
  </div>;
}

export function ResultFacts({ definitions, summary }: {
  definitions: ResultDecl[]; summary: Record<string, unknown>;
}) {
  const declarations = resultDeclarations(definitions);
  const names = declarations.length ? declarations.map(({ name }) => name) : Object.keys(summary);
  return <dl data-result-facts="" className="grid grid-cols-2 gap-x-6 gap-y-4 lg:grid-cols-3">
    {names.map((name) => {
      const definition = declarations.find((result) => result.name === name);
      const value = summary[name];
      const kind = definition?.type.kind;
      const numeric = typeof value === "number" && (kind === "int" || kind === "float" || kind === undefined);
      const formatted = kind === "bool" && typeof value === "boolean" ? (value ? "yes" : "no")
        : numeric ? fmtNum(value) : typeof value === "boolean" ? (value ? "yes" : "no") : value;
      return <div key={name} data-result-name={name} className="min-w-0">
        <dt title={definition?.description} className="mb-1 text-xs text-muted-foreground [overflow-wrap:anywhere]">
          {definition?.label || name}
        </dt>
        <dd className="text-lg font-medium tabular-nums [overflow-wrap:anywhere]">
          {Object.hasOwn(summary, name) ? <>{typeof formatted === "string" || typeof formatted === "number"
            ? formatted : <ParamChip value={formatted} />}
            {numeric && definition?.unit && <span className="ml-1 text-sm font-normal text-muted-foreground">{definition.unit}</span>}</>
            : <span aria-label="pending" className="text-muted-foreground">—</span>}
        </dd>
      </div>;
    })}
  </dl>;
}

/* Shared rows keep the explanation beside its result. Catalog rows omit values. */
export function ResultRows({ definitions, summary, metrics = [], scores = [], catalog = false,
  showPending = false, inlineDetails = false }: {
  definitions?: ResultDecl[];
  summary?: Record<string, unknown>;
  metrics?: MetricEv[];
  scores?: Record<string, unknown>[];
  catalog?: boolean;
  showPending?: boolean;
  inlineDetails?: boolean;
}) {
  const declarations = resultDeclarations(definitions);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const latest = new Map(dedupeMetrics(Array.isArray(metrics) ? metrics : []).map(m => [m.name, m]));
  const instanceValues = new Map<string, unknown[]>();
  for (const score of Array.isArray(scores) ? scores : [])
    for (const { name, value } of flattenScores(score))
      instanceValues.set(name, [...(instanceValues.get(name) ?? []), value]);
  const emitted = new Set([...Object.keys(summary ?? {}), ...latest.keys(), ...instanceValues.keys()]);
  const declared = declarations.map(({ name }) => name);
  const names = catalog ? declared
    : [...declared.filter(name => showPending || emitted.has(name)), ...[...emitted].filter(name => !declared.includes(name))];
  if (!names.length) return null;
  return (
    <dl className={cn("divide-y", !catalog && "result-value-rows")} data-result-rows="">
      {names.map(name => {
        const definition = declarations.find((result) => result.name === name);
        const label = definition?.label || name;
        const metric = latest.get(name);
        const unit = metric?.unit ?? definition?.unit;
        const hasValue = Object.hasOwn(summary ?? {}, name) || latest.has(name);
        const value = Object.hasOwn(summary ?? {}, name) ? summary![name] : metric?.value;
        const vals = instanceValues.get(name);
        const details = definition?.details?.trim();
        return (
          <div key={name} data-result-name={name} className="relative grid items-baseline py-2.5 first:pt-0 last:pb-0">
              <dt className="result-label text-sm font-medium [overflow-wrap:anywhere]">
                {details && !inlineDetails ? <button type="button" aria-expanded={Boolean(expanded[name])}
                  onClick={() => setExpanded(previous => ({ ...previous, [name]: !previous[name] }))}
                  className={cn("inline-flex max-w-full items-baseline gap-2 text-left hover:underline", !catalog && "sm:text-right")}>
                  <span aria-hidden="true" className="text-muted-foreground">{expanded[name] ? "▾" : "▸"}</span>
                  <span>{label}</span>
                </button> : label}
              </dt>
              {!catalog && (
                <dd className="result-value min-w-0 space-y-1 text-left font-mono text-sm [overflow-wrap:anywhere]">
                  {hasValue && <div>
                    <ResultValue value={value} />
                    {unit && <span className="ml-1 text-muted-foreground">{unit}</span>}
                    {(metric?.count ?? 0) > 1 && <span className="ml-2 text-muted-foreground" title="Same-named metric re-emitted; last value shown">×{metric!.count}</span>}
                  </div>}
                  {vals && <div className="text-muted-foreground">{instanceSummary(vals, unit)}</div>}
                  {showPending && !hasValue && !vals && <span className="text-muted-foreground">pending</span>}
                </dd>
              )}
            {catalog && unit && <dd className="mt-0.5 text-xs text-muted-foreground">Unit: {unit}</dd>}
            <dd className="result-description mt-1 whitespace-pre-line text-sm text-muted-foreground [overflow-wrap:anywhere]">
              {definition?.description?.trim() || "No description provided."}
            </dd>
            {details && (inlineDetails || expanded[name]) && <dd className="result-details mt-2 space-y-2 text-xs text-muted-foreground [overflow-wrap:anywhere]">
              <p className="whitespace-pre-line leading-relaxed">{details}</p>
              <p>Result key: <code>{name}</code></p>
            </dd>}
          </div>
        );
      })}
    </dl>
  );
}

function ResultValue({ value }: { value: unknown }) {
  if (typeof value === "boolean") return <b className="font-semibold">{value ? "Yes" : "No"}</b>;
  if (typeof value === "number") return <b className="font-semibold">{fmtNum(value)}</b>;
  if (typeof value === "string" && value.length <= 64 && !value.includes("\n")) return <span>{value}</span>;
  return <ParamChip value={value} />;
}

function instanceSummary(vals: unknown[], unit?: string | null): string {
  const n = vals.length;
  if (vals.every(v => typeof v === "boolean")) return `${vals.filter(Boolean).length}/${n} Yes · instances`;
  if (vals.every(v => typeof v === "number"))
    return `mean ${fmtNum((vals as number[]).reduce((a, b) => a + b, 0) / n)}${unit ? ` ${unit}` : ""} · n=${n} instances`;
  const counts = new Map<string, number>();
  for (const value of vals) {
    const text = typeof value === "string" ? value : JSON.stringify(value);
    counts.set(text, (counts.get(text) ?? 0) + 1);
  }
  return `${counts.size <= 3 ? [...counts].map(([v, c]) => `${v}×${c}`).join(" · ") : `${counts.size} distinct values`} · n=${n} instances`;
}

export function ExperimentResults({ definitions }: { definitions?: ResultDecl[] }) {
  if (!resultDeclarations(definitions).length) return null;
  return <details className="rounded-md border px-3 py-2">
    <summary className="cursor-pointer text-sm font-medium">Results this experiment records</summary>
    <div className="mt-3 max-h-80 overflow-y-auto pr-1">
      <ResultRows definitions={definitions} catalog />
    </div>
  </details>;
}

export function ResultChip({ name, value, unit, definition }: {
  name: string; value: unknown; unit?: string | null; definition?: ResultDecl;
}) {
  const label = definition?.label ?? name;
  const resolvedUnit = unit ?? definition?.unit;
  const title = definition?.description;

  if (typeof value === "boolean")
    return (
      <span className={cn(BASE, NEUTRAL)} title={title}>
        {label}: <b className="font-semibold">{value ? "Yes" : "No"}</b>
      </span>
    );
  if (typeof value === "number")
    return (
      <span className={cn(BASE, NEUTRAL)} title={title}>
        {label} <b className="font-semibold">{fmtNum(value)}</b>
        {resolvedUnit ? <span className="text-muted-foreground">{resolvedUnit}</span> : null}
      </span>
    );
  return <span title={title}><ParamChip name={label} value={value} /></span>;
}

/* Reader-derived values with optional observed result values, deduped by name. */
export function ResultChips({ summary, metrics, definitions, mobileLimit }: {
  definitions?: ResultDecl[];
  summary?: Record<string, unknown>;
  metrics?: { name: string; value: unknown; unit?: string | null }[];
  mobileLimit?: number;
}) {
  const declarations = resultDeclarations(definitions);
  const latest = new Map((Array.isArray(metrics) ? metrics : []).map((metric) => [metric.name, metric]));
  const values = { ...Object.fromEntries([...latest].map(([name, metric]) => [name, metric.value])), ...summary };
  const declared = declarations.map(({ name }) => name);
  const names = [...declared.filter((name) => Object.hasOwn(values, name)),
    ...Object.keys(values).filter((name) => !declared.includes(name))];
  if (!names.length) return null;
  return <>{names.map((name, index) => <span key={name}
    className={cn("contents", mobileLimit !== undefined && index >= mobileLimit && "max-md:hidden")}>
    <ResultChip name={name} value={values[name]}
      definition={declarations.find((result) => result.name === name)} unit={latest.get(name)?.unit} />
  </span>)}
    {mobileLimit !== undefined && names.length > mobileLimit && <span
      className={cn(BASE, NEUTRAL, "md:hidden")} aria-label={`${names.length - mobileLimit} more results on the run page`}>
      +{names.length - mobileLimit}
    </span>}
  </>;
}

/* scorer values flattened to leaves. New streams arrive pre-flattened (the spec's
   flat scalar map); legacy streams carry dict-valued scores (agentharm's
   combined_scorer {score, refusal}) which flatten here with the same '/' join */
export function flattenScores(scores: Record<string, unknown>): { name: string; value: unknown }[] {
  return Object.entries(scores).flatMap(([scorer, v]) =>
    v !== null && typeof v === "object" && !Array.isArray(v)
      ? Object.entries(v as Record<string, unknown>).map(([k, sv]) => ({ name: `${scorer}/${k}`, value: sv }))
      : [{ name: scorer, value: v }]);
}

export interface MetricEv { name: string; value: unknown; unit?: string | null }

/* repeated metric names collapse last-value-wins (the stream convention for
   run-level metrics), keeping the repeat count so nothing hides silently */
export function dedupeMetrics(ms: MetricEv[]): (MetricEv & { count: number })[] {
  const by = new Map<string, MetricEv & { count: number }>();
  for (const m of ms) {
    const prev = by.get(m.name);
    by.set(m.name, { ...m, count: (prev?.count ?? 0) + 1 });
  }
  return [...by.values()];
}

/* read-time aggregate over per-instance scores (instance close-outs), same
   language as AggChips: booleans → neutral Yes counts; numerics → neutral mean
   (a 0/1 numeric is NOT judged pass/fail — refusal=1 is good on agentharm's
   harmful split and bad on the benign one); other values → distinct counts.
   Derived at read time, stored nowhere (docs/book/src/reference/events.md). */
export function InstanceScoreChips({ scores, definitions }: {
  scores: Record<string, unknown>[]; definitions?: ResultDecl[];
}) {
  const declarations = resultDeclarations(definitions);
  const byKey = new Map<string, unknown[]>();
  for (const s of Array.isArray(scores) ? scores : [])
    for (const { name, value } of flattenScores(s))
      byKey.set(name, [...(byKey.get(name) ?? []), value]);
  return (
    <>
      {[...declarations.map(({ name }) => name).filter((name) => byKey.has(name)),
        ...[...byKey.keys()].filter((name) => !declarations.some((result) => result.name === name))].map((k) => {
        const vals = byKey.get(k)!;
        const definition = declarations.find((result) => result.name === k);
        const n = vals.length;
        const label = definition?.label ?? k;
        if (vals.every((v) => typeof v === "boolean")) {
          const p = vals.filter(Boolean).length;
          return <span key={k} className={cn(BASE, NEUTRAL)} title={`${p} of ${n} instances reported true`}>{label} {p}/{n} Yes</span>;
        }
        if (vals.every((v) => typeof v === "number")) {
          const mean = (vals as number[]).reduce((a, b) => a + b, 0) / n;
          return (
            <span key={k} className={cn(BASE, NEUTRAL)} title={`mean over ${n} instances`}>
              {label} x̄ <b className="font-semibold">{fmtNum(mean)}</b>
              {definition?.unit && <span>{definition.unit}</span>}
              <span className="text-muted-foreground">n={n}</span>
            </span>
          );
        }
        const counts = new Map<string, number>();
        for (const v of vals) {
          const s = typeof v === "string" ? v : JSON.stringify(v);
          counts.set(s, (counts.get(s) ?? 0) + 1);
        }
        return (
          <span key={k} className={cn(BASE, NEUTRAL)} title={`${n} instances`}>
            {label}{" "}
            {counts.size <= 3
              ? [...counts.entries()].map(([v, c]) => `${v}×${c}`).join(" ")
              : `×${n} values`}
          </span>
        );
      })}
    </>
  );
}

/* aggregate chips over a set of runs (matrix cells): n, per-boolean Yes counts,
   per-numeric means — same chip language as single results */
export function AggChips({ runs }: { runs: RunMeta[] }) {
  const done = (Array.isArray(runs) ? runs : []).filter((r) => r.readable !== false && r.state === "completed");
  const emitted = new Set(done.flatMap((r) => Object.keys(r.summary ?? {})));
  const declared = done.flatMap((r) => resultDeclarations(r.result_definitions).map(({ name }) => name));
  const keys = [...new Set([...declared.filter((name) => emitted.has(name)), ...emitted])];
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <span className={cn(BASE, NEUTRAL, "text-muted-foreground")}>n={runs.length}</span>
      {keys.map((k) => {
        const vals = done.map((r) => (r.summary ?? {})[k]).filter((v) => v !== undefined);
        if (vals.length && vals.every((v) => typeof v === "boolean")) {
          const p = vals.filter(Boolean).length;
          return <span key={k} className={cn(BASE, NEUTRAL)}>{k} {p}/{vals.length} Yes</span>;
        }
        if (vals.length && vals.every((v) => typeof v === "number"))
          return (
            <span key={k} className={cn(BASE, NEUTRAL)}>
              {k} x̄ {fmtNum((vals as number[]).reduce((a, b) => a + b, 0) / vals.length)}
            </span>
          );
        return null;
      })}
    </span>
  );
}
