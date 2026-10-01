/* Shared boundary facts have dedicated views; custom kinds continue to use hints. */
import { useState, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import type { Ev, ParamDecl, ParamType, ResultDecl } from "@/shared/types";
import { containsElision } from "@/lib/event-transport";
import { exitLabel, fetchRefHref, storePackage } from "@/lib/event-display";
import { duration } from "@/lib/run-view";
import { StateBadge } from "@/components/bits";
import { fmtNum } from "@/components/results";

function CopyValue({ value, label, display = value }: { value: string; label: string; display?: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState(false);
  return <span className="inline-flex max-w-full items-start gap-1.5">
    <code title={value} className="break-all text-xs">{display}</code>
    <button type="button" aria-label={`Copy ${label}`} title={copied ? "Copied" : `Copy ${label}`}
      className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-accent"
      onClick={async () => {
        try { await navigator.clipboard.writeText(value); setCopied(true); setError(false); }
        catch { setError(true); }
      }}>{copied ? <Check className="size-3" /> : <Copy className="size-3" />}</button>
    {error && <span role="status" className="text-xs text-destructive">Copy unavailable</span>}
  </span>;
}

/** Typed values stay structured, including nested parameters, without JSON blocks. */
export function FieldValue({ value, type }: { value: unknown; type?: ParamType }) {
  if (containsElision(value)) return <span className="text-muted-foreground">Loading recorded value…</span>;
  if (value === undefined) return <span className="text-muted-foreground">not recorded</span>;
  if (value === null) return <span className="font-mono text-muted-foreground">null</span>;
  if (typeof value === "boolean") return <>{value ? "yes" : "no"}</>;
  if (typeof value === "number") return <>{type?.kind === "int" ? String(value) : fmtNum(value)}</>;
  if (Array.isArray(value)) return value.length ? <ul className="list-inside list-disc space-y-1">
    {value.map((entry, index) => <li key={index}><FieldValue value={entry} type={type?.of} /></li>)}</ul>
    : <span className="text-muted-foreground">empty list</span>;
  if (typeof value === "object") return <dl className="space-y-1">
    {Object.entries(value).map(([key, entry]) => {
      const field = type?.fields?.[key];
      return <div key={key} className="flex flex-wrap gap-x-2">
        <dt className="text-muted-foreground">{key}:</dt><dd><FieldValue value={entry}
          type={field && ("type" in field ? field.type : field)} /></dd>
      </div>;
    })}{!Object.keys(value).length && <div className="text-muted-foreground">empty object</div>}
  </dl>;
  return <span className="whitespace-pre-wrap [overflow-wrap:anywhere]">{String(value)}</span>;
}

const short = (value: string) => value.replace(/^content:sha256:|^sha256-/, "").slice(0, 12);
function Facts({ values }: { values: [string, ReactNode][] }) {
  return <dl className="space-y-2 text-sm">{values.map(([label, value]) =>
    <div key={label} className="grid items-baseline gap-1 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-3">
      <dt className="text-xs text-muted-foreground">{label}</dt><dd className="min-w-0 break-words">{value}</dd>
    </div>)}</dl>;
}

export function RunStartCard({ record, summaryHref, paramDeclarations = {} }: {
  record: Ev; summaryHref: string; paramDeclarations?: Record<string, ParamDecl>;
}) {
  const start = record.event;
  const runtime = start.runtime ?? {};
  const ref = typeof start.fetch_ref === "string" ? start.fetch_ref : null;
  const refHref = ref && fetchRefHref(ref);
  const copy = (label: string, value: unknown, abbreviate = false) => typeof value === "string"
    ? <CopyValue label={label} value={value} display={abbreviate ? short(value) : value} /> : <FieldValue value={value} />;
  return <section data-run-start-card="" className="space-y-4 rounded-md border bg-muted/20 p-3">
    <div className="grid gap-5 xl:grid-cols-2">
      <section><h4 className="mb-2 text-xs font-semibold">Identity</h4><Facts values={[
        ["Experiment", record.experiment], ["Condition", copy("condition", start.condition, true)],
        ["Run", <code className="text-xs">{record.run}</code>],
        ["Seed", <FieldValue value={start.seed} type={{ kind: "int" }} />],
      ]} /></section>
      <section><h4 className="mb-2 text-xs font-semibold">Provenance</h4><Facts values={[
        ["Source", copy("source", start.source, true)],
        ["Revision", ref ? refHref ? <a href={refHref} target="_blank" rel="noreferrer" className="break-all text-xs underline">{ref}</a>
          : <code className="break-all text-xs">{ref}</code> : "no pinned revision"],
        ["Tree hash", copy("tree hash", start.tree_hash, true)],
      ]} /></section>
    </div>
    <section><h4 className="mb-2 text-xs font-semibold">Runtime</h4><Facts values={[
      ["Platform", <FieldValue value={runtime.platform} />],
      ["Python", <FieldValue value={runtime.runner_python_version} />],
      ...(["experiment_bin", "runner_bin"] as const).flatMap((key): [string, ReactNode][] =>
        typeof runtime[key] === "string" ? [[key === "experiment_bin" ? "Experiment bin" : "Runner bin",
          <CopyValue label={key} value={runtime[key]} display={storePackage(runtime[key])} />]] : []),
      ...Object.entries(runtime.endpoints ?? {}).map(([name, origin]): [string, ReactNode] =>
        [`Endpoint: ${name}`, <FieldValue value={origin} />]),
    ]} /></section>
    <section><h4 className="mb-2 text-xs font-semibold">Inputs</h4>
      {Object.keys(start.params ?? {}).length ? <Facts values={Object.entries(start.params).map(([name, value]) =>
        [name, <FieldValue value={value} type={paramDeclarations[name]?.type} />])} />
        : <p className="text-xs text-muted-foreground">No parameters recorded.</p>}
    </section>
    <a href={summaryHref} className="inline-block text-sm underline">{start.result_definitions?.length ?? 0} declared results · Summary</a>
  </section>;
}

export function RunEndFacts({ record, platform }: {
  record: Ev; platform?: string;
}) {
  return <div data-run-end-facts="" className="space-y-2 rounded-md border bg-muted/20 p-3 text-sm">
    <div className="flex flex-wrap items-center gap-2"><StateBadge state={record.event.state} />
      <span>duration {duration(record.event.duration_s)}</span>
      <span>{exitLabel(record.event.exit_code, platform)}</span></div>
  </div>;
}

export function SharedResult({ name, value, definition }: { name: string; value: unknown; definition?: ResultDecl }) {
  return <span className="inline-flex flex-wrap items-baseline gap-2" title={name}>
    <span className="font-medium">{typeof definition?.label === "string" ? definition.label : name}</span>
    <span className="tabular-nums"><FieldValue value={value} type={definition?.type} /></span>
    {typeof definition?.unit === "string" && <span className="text-muted-foreground">{definition.unit}</span>}
  </span>;
}
