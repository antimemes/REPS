/* Run summary and stream share one event snapshot and URL navigation state. */

import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { Ev, FullEvent, Manifest, RunMeta } from "@/shared/types";
import { publishedMode } from "@/lib/data-source";
import { displayState, fetchRunJson, fmtVal, loadRunEvents, runCache, useManifests, useRunSchemas, useRunsPoll } from "@/lib/data";
import { LiveDot, LoadingBar, StateBadge, Skeleton } from "@/components/bits";
import { RunResults } from "@/components/results";
import { EventStream } from "@/components/event-stream";
import { RenderBoundary, UnreadableRunPanel } from "@/components/read-errors";
import { oneLineReason, resultDeclarations } from "@/lib/run-readability";
import { ParamChip } from "@/components/param-value";
import { cn } from "@/lib/utils";
import { compileSchemas, kindNamespace, schemaKinds, type EventDefinition } from "@/lib/render-hints";
import { duration, elapsedSeconds, matchesRunFilter, readRunTab, readRunView, rememberRunTab,
  runActivity, runHref, selectRunTab, type RunTab, type StreamFilter } from "@/lib/run-view";

export function RunPage(props: { cid: string; rid: string; query?: string }) {
  return <RenderBoundary key={`${props.cid}/${props.rid}`} fallback={(reason) => <UnreadableRunPanel {...props} reason={reason} />}>
    <RunPageData {...props} />
  </RenderBoundary>;
}

function RunPageData({ cid, rid, query = "" }: { cid: string; rid: string; query?: string }) {
  const key = `${cid}/${rid}`;
  const eventsRef = useRef<Ev[]>(runCache[key]?.events ?? []);
  const lastSeqRef = useRef(runCache[key]?.lastSeq ?? -1);
  const [, bump] = useReducer((x: number) => x + 1, 0);
  const [readError, setReadError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now);
  const [preferredTab, setPreferredTab] = useState(() => readRunTab(localStorage));
  const explicitTab = readRunView(query).tab;
  useEffect(() => {
    if (!explicitTab) return;
    rememberRunTab(localStorage, explicitTab);
    setPreferredTab(explicitTab);
  }, [explicitTab]);
  const meta = useRunsPoll()?.find((r) => r.condition === cid && r.run === rid) ?? null;
  const manifests = useManifests();

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setInterval> | null = null;
    const done = () => eventsRef.current.some((ev) => ev.event.type === "run.end");
    const poll = async () => {
      let fresh: Ev[];
      try {
        fresh = await loadRunEvents(cid, rid, lastSeqRef.current);
      } catch (error) { if (!stopped) setReadError(oneLineReason(error)); return; }
      setReadError(null);
      if (stopped) return;
      if (fresh.length) {
        eventsRef.current = [...eventsRef.current, ...fresh];
        lastSeqRef.current = fresh[fresh.length - 1]!.seq ?? lastSeqRef.current;
        runCache[key] = { events: eventsRef.current, lastSeq: lastSeqRef.current };
        bump();
      }
      if (done() && timer !== null) { clearInterval(timer); timer = null; }
    };
    void poll().then(() => {
      if (!stopped && !done()) timer = setInterval(() => void poll(), 2000);
    });
    return () => { stopped = true; if (timer !== null) clearInterval(timer); };
  }, [cid, rid, key]);

  const events = eventsRef.current;
  const ended = events.some((e) => e.event.type === "run.end");
  useEffect(() => {
    if (ended) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ended]);
  const schemas = useRunSchemas(cid, rid, events.length > 0);
  const definitions = useMemo(() => compileSchemas(schemas), [schemas]);
  const experiment = events.find((e) => typeof e.experiment === "string")?.experiment
    ?? meta?.experiment;
  if (!events.length && !meta && !readError)
    return <div className="space-y-3 pt-1"><LoadingBar /><Skeleton className="h-16" />
      <Skeleton className="h-8 w-72" /><Skeleton className="h-64" /></div>;
  return <RunView cid={cid} rid={rid} query={query} preferredTab={preferredTab} events={events}
    definitions={definitions} meta={meta} now={now}
    manifest={manifests?.find((m) => m.name === experiment)} readError={readError} />;
}

/* Data-only view is also rendered by the regression guard and review exporter. */
export function RunView(props: Parameters<typeof ReadableRunView>[0]) {
  const fallback = (reason: string) => <UnreadableRunPanel cid={props.cid} rid={props.rid} reason={reason} rawRunJson={props.rawRunJson} />;
  const reason = props.meta?.readable === false ? props.meta.reason ?? "Run could not be read" : props.readError;
  if (reason) return fallback(reason);
  if (!Array.isArray(props.events)) return fallback("Events must be an array");
  return <RenderBoundary resetKey={props.events} fallback={fallback}><ReadableRunView {...props} /></RenderBoundary>;
}

function ReadableRunView({ cid, rid, query = "", preferredTab = null, events, definitions = [],
  meta, manifest, now = Date.now(), review = false, readError, diskRecords }: {
  cid: string; rid: string; query?: string; preferredTab?: RunTab | null;
  events: Ev[]; definitions?: EventDefinition[]; meta?: RunMeta | null;
  manifest?: Manifest; now?: number; review?: boolean; readError?: string | null;
  diskRecords?: ReadonlyMap<number, FullEvent>; rawRunJson?: string | null;
}) {
  const startRecord = events.find((e) => e.event.type === "run.start");
  const start = startRecord?.event;
  const end = events.find((e) => e.event.type === "run.end")?.event;
  const state = end?.state ?? (meta ? displayState(meta, now) : "running");
  const terminal = !!end || ["completed", "failed", "interrupted"].includes(state);
  const experiment = events.find((e) => typeof e.experiment === "string")?.experiment
    ?? meta?.experiment ?? manifest?.name ?? "run";
  const options = readRunView(query);
  const tab = selectRunTab(options, preferredTab, terminal);
  const activity = {
    kinds: new Map(Object.entries(meta?.derived?.counts.by_kind ?? {})),
    actors: runActivity(events, definitions).actors,
  };
  const visible = events.filter((e) => matchesRunFilter(e, options.filter, definitions));
  const href = (nextTab: RunTab, filter = options.filter) => runHref(cid, rid, { tab: nextTab, filter });
  const resultDefinitions = resultDeclarations(meta?.result_definitions ?? manifest?.results);
  const summary = meta?.derived?.results ?? {};
  const usage = { calls: meta?.derived?.counts.llm_calls ?? 0, failed: meta?.derived?.counts.failed_calls ?? 0,
    input: meta?.derived?.usage.input_tokens ?? 0, output: meta?.derived?.usage.output_tokens ?? 0 };
  const runtime = start?.runtime ?? meta?.runtime ?? {};
  const params = meta?.params ?? start?.params;
  const lastStatus = meta?.derived?.last_status;
  const elapsed = end?.duration_s ?? meta?.duration_s ?? elapsedSeconds(startRecord?.ts ?? meta?.started_at ?? events[0]?.ts, now);
  const age = elapsedSeconds(meta?.derived?.last_event_at, now);
  const failedCalls = <span className={usage.failed ? "font-medium text-destructive" : "text-muted-foreground"}>
    {usage.failed} failed calls</span>;
  return <div className="flex h-full min-h-0 flex-col gap-3" data-run-page="">
    <header className="shrink-0 space-y-1" data-run-header="">
      <h2 className="flex flex-wrap items-center gap-2 text-base font-semibold">
        <a href={`#/experiments/${encodeURIComponent(experiment)}`} className="hover:underline">{experiment}</a>
        <StateBadge state={state} />
      </h2>
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <a href={`#/conditions/${encodeURIComponent(cid)}`} title={cid} className="hover:underline">condition <span className="font-mono">{cid.slice(0, 12)}</span></a>
        <span>run <span className="break-all font-mono">{rid}</span></span>
        <span>seed <b className="font-medium text-foreground">{fmtVal(start?.seed ?? meta?.seed)}</b></span>
      </div>
    </header>
    {readError && <p role="alert" className="text-sm text-destructive">{readError}</p>}
    <nav aria-label="Run tabs" className="flex shrink-0 gap-4 border-b">
      {(["summary", "stream"] as const).map((name) => <a key={name} href={href(name)}
        aria-current={tab === name ? "page" : undefined} data-tab={name}
        className={cn("border-b-2 px-1 pb-2 text-sm max-md:min-h-10 max-md:py-2", tab === name
          ? "border-primary font-medium text-foreground" : "border-transparent text-muted-foreground hover:text-foreground")}>
        {name === "summary" ? "Summary" : "Stream"}
      </a>)}
    </nav>
    {tab === "summary" ? <div className="min-h-0 flex-1 space-y-6 overflow-y-auto pb-6 pr-1" data-run-tab="summary">
      <section aria-label={terminal ? "Outcome" : "Progress"} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <StateBadge state={state} />
        <span>{terminal ? "duration" : "elapsed"} {duration(elapsed)}</span>
        {!terminal && lastStatus && <span>{lastStatus}</span>}
        <span>{usage.calls} calls</span><span>{usage.input}+{usage.output} tokens</span>
        {failedCalls}
        {!terminal && <><LiveDot state={state} /><span className="text-muted-foreground">
          {age === null ? "waiting for first event" : `last event ${duration(age)} ago`}</span>
          {state === "interrupted?" && <span className="text-amber-700 dark:text-amber-400">possibly interrupted</span>}</>}
      </section>
      <section aria-label="Results">
        <SectionTitle>Results</SectionTitle>
        {Object.keys(resultDefinitions).length || Object.keys(summary).length
          ? <RunResults definitions={resultDefinitions} summary={summary} />
          : <p className="text-sm text-muted-foreground">No results declared.</p>}
      </section>
      <section aria-label="Activity">
        <SectionTitle>Activity</SectionTitle>
        <div className="space-y-3 text-sm">
          <ActivityLinks label="By kind" entries={[...activity.kinds].map(([kind, count]) => ({ id: kind, label: kind, count }))}
            link={(id) => href("stream", { by: "kind", value: id })} />
          <ActivityLinks label="By actor" entries={activity.actors}
            link={(id) => href("stream", { by: "actor", value: id })} />
        </div>
      </section>
      <section aria-label="Inputs">
        <SectionTitle>Inputs</SectionTitle>
        {params && Object.keys(params).length ? <Facts values={params} />
          : <p className="text-sm text-muted-foreground">Parameters have not been recorded.</p>}
      </section>
      <section aria-label="Provenance">
        <SectionTitle>Provenance</SectionTitle>
        <Facts values={{ source: start?.source ?? meta?.source ?? "not recorded",
          fetch_ref: start?.fetch_ref ?? meta?.fetch_ref ?? "no pinned revision",
          tree_hash: start?.tree_hash ?? meta?.tree_hash ?? "not recorded" }} />
        <h4 className="mb-2 mt-4 text-xs font-medium">Runtime</h4>
        <Facts values={Object.fromEntries(Object.entries(runtime).filter(([key]) => key !== "endpoints"))} />
        <h4 className="mb-2 mt-4 text-xs font-medium">Endpoint origins</h4>
        {Object.keys(runtime.endpoints ?? {}).length ? <Facts values={runtime.endpoints} />
          : <p className="text-sm text-muted-foreground">No endpoint origins recorded.</p>}
      </section>
      {publishedMode() && <RawRunCard cid={cid} rid={rid} />}
    </div> : <div className="min-h-0 flex-1" data-run-tab="stream">
      <EventStream events={events} visibleEvents={visible} definitions={definitions} state={state} cid={cid} rid={rid} review={review}
        paramDeclarations={manifest?.params}
        diskRecords={diskRecords}
        facets={<RunFacets events={events} activity={activity} definitions={definitions} filter={options.filter}
          link={(filter) => href("stream", filter)} />} />
    </div>}
  </div>;
}

function SectionTitle({ children }: { children: string }) {
  return <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{children}</h3>;
}

function Facts({ values }: { values: Record<string, unknown> }) {
  return <dl className="space-y-2 text-sm">{Object.entries(values).map(([key, value]) =>
    <div key={key} className="grid items-baseline gap-1 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-3">
      <dt className="font-mono text-xs text-muted-foreground">{key}</dt>
      <dd className="min-w-0 break-words"><ParamChip value={value} /></dd>
    </div>)}</dl>;
}

function ActivityLinks({ label, entries, link }: {
  label: string; entries: { id: string; label: string; count: number }[]; link: (id: string) => string;
}) {
  return <div><h4 className="mb-1.5 text-xs text-muted-foreground">{label}</h4>
    <div className="flex flex-wrap gap-2">{(Array.isArray(entries) ? entries : []).map((entry) =>
      <a key={entry.id} href={link(entry.id)} title={entry.id} className="rounded border px-2 py-1 hover:bg-accent">
        {entry.label} <span className="ml-1 font-mono tabular-nums text-muted-foreground">{entry.count}</span>
      </a>)}{!entries.length && <span className="text-muted-foreground">None recorded.</span>}</div>
  </div>;
}

function RunFacets({ events, activity, definitions, filter, link }: {
  events: Ev[]; activity: ReturnType<typeof runActivity>; definitions: EventDefinition[];
  filter: StreamFilter; link: (filter: StreamFilter) => string;
}) {
  const kinds = schemaKinds(Array.isArray(definitions) ? definitions : []).filter((kind) => events.some((event) =>
    matchesRunFilter(event, { by: "kind", value: kind }, definitions)));
  if (filter?.by === "kind" && !kinds.includes(filter.value)) kinds.push(filter.value);
  const namespace = filter?.by === "namespace" ? filter.value
    : filter?.by === "kind" ? kindNamespace(filter.value) : null;
  type Facet = { filter: StreamFilter; label: string };
  const roots = new Map<string, Facet>();
  for (const kind of kinds) {
    const prefix = kindNamespace(kind);
    const facet = prefix ? { filter: { by: "namespace", value: prefix } as const, label: prefix }
      : { filter: { by: "kind", value: kind } as const, label: kind };
    roots.set(`${facet.filter.by}:${facet.filter.value}`, facet);
  }
  if (namespace && !roots.has(`namespace:${namespace}`))
    roots.set(`namespace:${namespace}`, { filter: { by: "namespace", value: namespace }, label: namespace });
  const actors: Facet[] = (Array.isArray(activity.actors) ? activity.actors : []).map((actor) => ({ filter: { by: "actor", value: actor.id }, label: actor.label }));
  if (filter?.by === "actor" && !activity.actors.some((a) => a.id === filter.value))
    actors.push({ filter, label: filter.value });
  const chip = (facet: Facet) => {
    const key = facet.filter ? `${facet.filter.by}:${facet.filter.value}` : "all";
    const active = (facet.filter?.by === filter?.by && facet.filter?.value === filter?.value)
      || (facet.filter?.by === "namespace" && facet.filter.value === namespace);
    return <a key={key} href={link(facet.filter)} data-filter={key} aria-current={active ? "true" : undefined}
      title={facet.filter ? `${facet.filter.by}: ${facet.filter.value}` : "All events"}
      className={cn("rounded-full border px-2.5 py-0.5 max-md:inline-flex max-md:items-center max-md:py-1.5", active ? "border-primary bg-primary/10 text-foreground"
        : "border-transparent text-muted-foreground hover:border-ring hover:text-foreground")}>
      {facet.filter?.by === "actor" && <span className="text-muted-foreground">actor: </span>}{facet.label}
    </a>;
  };
  return <nav aria-label="Stream filters" className="max-h-48 shrink-0 space-y-1.5 overflow-y-auto border-b px-2.5 py-2 text-xs">
    <div className="flex flex-wrap items-center gap-1.5" data-facet-level="namespaces">
      {[{ filter: null, label: "all" }, ...roots.values()].map(chip)}
    </div>
    {namespace && <div className="flex flex-wrap items-center gap-1.5 border-l-2 pl-2" data-facet-level="kinds" aria-label={`${namespace} kinds`}>
      {kinds.filter((kind) => kindNamespace(kind) === namespace)
        .map((kind) => chip({ filter: { by: "kind", value: kind }, label: kind }))}
    </div>}
    {!!actors.length && <div className="flex flex-wrap items-center gap-1.5" aria-label="Actor filters">{actors.map(chip)}</div>}
  </nav>;
}

/** The index is a cache; raw metadata always comes from the run object. */
function RawRunCard({ cid, rid }: { cid: string; rid: string }) {
  const [raw, setRaw] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  return <details data-raw-run-json="" onToggle={(event) => {
    if (event.currentTarget.open && raw === null && error === null)
      void fetchRunJson(cid, rid).then(setRaw).catch((error) => setError(oneLineReason(error)));
  }}>
    <summary className="cursor-pointer text-xs text-muted-foreground">raw run.json</summary>
    {raw === null ? <p>{error ?? "Loading run.json…"}</p>
      : <pre className="mt-2 overflow-auto whitespace-pre-wrap font-mono text-xs">{raw}</pre>}
  </details>;
}
