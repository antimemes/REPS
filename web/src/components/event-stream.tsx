/* Schema-driven event rows with rich model-call and lifecycle renderers. */

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  ArrowDown, CircleHelp, FileText, Flag, Info,
  Play, Sparkles, icons,
} from "lucide-react";
import type { Ev, FullEvent, ParamDecl, ResultDecl } from "@/shared/types";
import { fetchFullEvent, fmtVal, uiState } from "@/lib/data";
import { highlightJson } from "@/lib/markdown";
import { formatJsonText } from "@/lib/json-text";
import { duration, runHref } from "@/lib/run-view";
import { exitLabel, stripAnsi } from "@/lib/event-display";
import { FieldValue, RunEndFacts, RunStartCard, SharedResult } from "@/components/shared-event-rows";
import { LiveDot, MdView, StateBadge, Segmented } from "@/components/bits";
import { cn } from "@/lib/utils";
import { containsElision, splitContent } from "@/lib/content";

import { LLMCallMessages } from "@/components/llm-messages";
import { previousCalls } from "@/lib/llm-history";

import { needsDisplayRecord } from "@/lib/event-transport";
import { actorFor, actorLabels, fieldAt, hintBody, hintText, hintValue, renderHint, type EventDefinition, type RenderHint } from "@/lib/render-hints";
import { buildGutters, type GutterInfo, type GutterMode } from "@/lib/event-time";
export { buildGutters } from "@/lib/event-time";
export type { GutterInfo, GutterMode } from "@/lib/event-time";

/* ---------------- per-type styling ---------------- */

type Style = { Icon: typeof Info; badge: string; icon: string };

/* tailwind can't see computed class names — every hue is written out literally */
const STYLES: Record<string, Style> = {
  "run.start": { Icon: Play, badge: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400", icon: "text-emerald-600 dark:text-emerald-400" },
  "run.end": { Icon: Flag, badge: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400", icon: "text-emerald-600 dark:text-emerald-400" },
  status: { Icon: Info, badge: "border border-border/60 text-muted-foreground/80", icon: "text-muted-foreground" },
  log: { Icon: FileText, badge: "bg-slate-500/15 text-slate-700 dark:text-slate-400", icon: "text-slate-600 dark:text-slate-400" },
  stdout: { Icon: FileText, badge: "border border-border/60 text-muted-foreground/80", icon: "text-muted-foreground" },
  /* stderr is a channel, not a severity — docker/git/pip narrate progress there.
     Neutral like stdout (the badge label tells them apart); red stays reserved for
     log level=error and failed runs. */
  stderr: { Icon: FileText, badge: "border border-border/60 text-muted-foreground/80", icon: "text-muted-foreground" },
  "llm.call": { Icon: Sparkles, badge: "bg-violet-500/15 text-violet-700 dark:text-violet-400", icon: "text-violet-600 dark:text-violet-400" },
};
const FALLBACK: Style = { Icon: CircleHelp, badge: "bg-muted text-muted-foreground", icon: "text-muted-foreground" };

const RED = "bg-red-500/15 text-red-700 dark:text-red-400";
const RED_ICON = "text-red-600 dark:text-red-400";
const EMPTY_FLAG =
  "rounded border border-dashed border-amber-500/60 px-1 font-mono text-[10px] text-amber-700 dark:text-amber-400";

const Gutter = ({ g }: { g?: GutterInfo }) => {
  const seconds = g?.label.split(".")[0] ?? "";
  return <span title={g?.title} className="w-[9ch] shrink-0 select-none whitespace-pre text-right font-mono text-[10px] tabular-nums md:w-[16ch]">
    <span className="md:hidden">
      <span className="text-muted-foreground/40">{seconds.slice(0, g?.unchanged ?? 0)}</span>
      <span className="text-foreground/90">{seconds.slice(g?.unchanged ?? 0)}</span>
    </span>
    <span className="hidden md:inline">
      <span className="text-muted-foreground/40">{g?.label.slice(0, g.unchanged)}</span>
      <span className="text-foreground/90">{g?.label.slice(g.unchanged)}</span>
    </span>
  </span>;
};

function HintIcon({ hint, fallback = FALLBACK.Icon, className }: {
  hint: RenderHint | null; fallback?: typeof Info; className?: string;
}) {
  const name = hint?.icon.replace(/(^|-)([a-z])/g, (_match, _dash, letter: string) => letter.toUpperCase());
  const Icon = name ? icons[name as keyof typeof icons] ?? fallback : fallback;
  return <Icon className={className} />;
}

function Actor({ id, labels, label }: { id: string; labels: Map<string, string>; label?: string }) {
  return <span className="font-semibold" title={id}>{label ?? labels.get(id) ?? id}</span>;
}

function HintedSummary({ e, hint, labels }: { e: Ev; hint: RenderHint; labels: Map<string, string> }) {
  const id = hintText(fieldAt(e.event, hint.actor));
  const rowLabel = fieldAt(e.event, hint.actor_label);
  const label = typeof rowLabel === "string" && rowLabel.trim() ? rowLabel : undefined;
  const actor = label ?? labels.get(id) ?? id;
  const title = hintText(hintValue(e.event, hint.title));
  const body = hintBody(e.event, hint);
  return <>
    <span className="shrink-0 font-mono text-[10px] text-muted-foreground">{e.event.kind ?? e.event.type}</span>
    {id && <Actor id={id} labels={labels} label={label} />}
    {title && title !== actor && <span className="font-medium">{title}</span>}
    {hint.badge && Boolean(fieldAt(e.event, hint.badge)) && <span data-hint-badge="" className="rounded border px-1 text-[10px] text-muted-foreground">
      {hint.badge.split(".").at(-1)}</span>}
    {body && <span className="line-clamp-2 min-w-0 max-md:basis-full text-xs text-muted-foreground group-open/row:hidden">{body}</span>}
  </>;
}

function HintFields({ e, hint }: { e: Ev; hint: RenderHint }) {
  const fields = (hint.fields ?? []).map((path) => ({ path, value: fieldAt(e.event, path) }))
    .filter(({ value }) => value !== undefined);
  if (!fields.length) return null;
  return <dl data-hint-fields="" className="space-y-2 rounded-md border bg-muted/20 p-3 text-sm">
    {fields.map(({ path, value }) => <div key={path} className="grid gap-1 sm:grid-cols-[12rem_minmax(0,1fr)] sm:gap-3">
      <dt title={path} className="text-xs text-muted-foreground">{path.split(".").at(-1)?.replaceAll("_", " ")}</dt>
      <dd className="min-w-0"><FieldValue value={value} /></dd>
    </div>)}
  </dl>;
}

/* Hinted actor identities supply row labels. Model facts belong to each call. */
export interface AgentInfo {
  id: string;
  name: string;
}

export interface StreamProfile {
  labels: Map<string, string>;
  agentCount: number;
  showAgent: boolean;
  /* default gutter mode — constant today; the seam a per-experiment manifest
     hint will set later (same precursor pattern as showAgent) */
  gutterMode: GutterMode;
  /* Actor IDs and display names from the stream's rendering hints. */
  agents: AgentInfo[];
}
export function deriveProfile(events: Ev[], definitions: EventDefinition[] = []): StreamProfile {
  const labels = actorLabels(events, definitions);
  const agents = new Map<string, AgentInfo>();
  for (const e of events) {
    const actor = actorFor(e, definitions);
    if (actor !== null) {
      agents.set(actor, { id: actor, name: labels.get(actor) ?? actor });
    }
  }
  return {
    labels,
    agentCount: agents.size,
    showAgent: agents.size > 1,
    gutterMode: "absolute",
    agents: [...agents.values()],
  };
}

/* per-call facts (model, tokens, latency, provenance) appear in the open row's
   header, replacing its collapsed preview. */
function factsStr(e: Ev): string {
  const u = e.event.output?.usage ?? {};
  const served = e.event.output?.model;
  const model =
    typeof served === "string" && served !== e.event.model
      ? `${String(e.event.model ?? "")} → ${served}` /* requested → what the provider reported serving */
      : String(e.event.model ?? "");
  return `${model} · ${fmtVal(u.input_tokens)}+${fmtVal(u.output_tokens)} tok · ${fmtVal(e.event.working_time != null ? e.event.working_time * 1000 : undefined)}ms`
    + `${e.event.via ? ` · via ${String(e.event.via)}` : ""}`;
}
const JsonPre = ({ src, className }: { src: string; className?: string }) => (
  <pre
    className={cn(
      "max-h-64 overflow-y-auto whitespace-pre-wrap rounded-md border bg-muted/50 px-2.5 py-1.5 font-mono text-xs [overflow-wrap:anywhere]",
      className,
    )}
    dangerouslySetInnerHTML={{ __html: highlightJson(src) }}
  />
);

/** Both views start from the served disk text; formatted changes whitespace only. */
export function RawEvent({ line }: { line: string }) {
  const [mode, setMode] = useState("formatted");
  const formatted = useMemo(() => formatJsonText(line), [line]);
  const className = "max-h-96 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/50 px-2.5 py-1.5 font-mono text-xs [overflow-wrap:anywhere]";
  return <div data-raw-view="" className="space-y-1">
    <div aria-label="Raw event presentation" className="flex justify-end">
      <Segmented options={["formatted", "disk line"]} value={mode} onChange={setMode} />
    </div>
    <div hidden={mode !== "formatted"}><pre data-raw-event="formatted" className={className}
      dangerouslySetInnerHTML={{ __html: highlightJson(formatted) }} /></div>
    <div hidden={mode !== "disk line"}><pre data-raw-event="disk line" className={className}>{line}</pre></div>
  </div>;
}

const toolsSummary = (chips: { name: string; primary: string }[]): string =>
  chips.map((c) => `${c.name} ${c.primary}`.trim()).join(", ");

function toolName(tc: Record<string, any>): string {
  return String(typeof tc.function === "string" ? tc.function : tc.function?.name ?? tc.name ?? tc.tool ?? "tool");
}
/* what the tool acted on — shown in the collapsed chip/row header itself:
   path-shaped tools → path/file/filename, shell-shaped → command, else the first
   string-valued argument */
function primaryArg(name: string, args: unknown): string {
  let a: Record<string, any> | undefined;
  if (typeof args === "string") {
    try { a = JSON.parse(args) as Record<string, any>; } catch { return args; }
  } else if (args && typeof args === "object" && !Array.isArray(args)) a = args as Record<string, any>;
  if (!a) return "";
  const strOf = (keys: string[]) => {
    for (const k of keys) if (typeof a![k] === "string") return a![k] as string;
    return undefined;
  };
  if (/(read|write|edit|open|view|cat|file)/i.test(name)) {
    const p = strOf(["path", "file", "filename", "file_path"]);
    if (p) return p;
  }
  if (/(bash|exec|shell|run|cmd|terminal)/i.test(name)) {
    const c = strOf(["command", "cmd", "script"]);
    if (c) return c;
  }
  const generic = strOf(["path", "file", "filename", "command", "query", "url"]);
  if (generic) return generic;
  const first = Object.values(a).find((v) => typeof v === "string");
  return typeof first === "string" ? first : "";
}

/* ---------------- one event row ---------------- */

interface SharedContext {
  previousCalls: Map<number, Ev>;
  results: ResultDecl[];
  latestStatus?: number;
  platform?: string;
  summaryHref: string;
  params?: Record<string, ParamDecl>;
}

function Row({ e: eProp, definitions, profile, gutter, fetchFull, context, diskRecord }: {
  e: Ev;
  definitions: EventDefinition[];
  profile: StreamProfile;
  gutter?: GutterInfo;
  fetchFull?: (seq: number) => Promise<FullEvent>;
  context: SharedContext;
  diskRecord?: FullEvent;
}) {
  const [full, setFull] = useState<FullEvent | null>(diskRecord ?? null);
  const [loadingFull, setLoadingFull] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const e = full?.record ?? eProp;
  const ensureFull = () => {
    if (full || !fetchFull || loadingFull) return;
    setLoadingFull(true);
    setReadError(null);
    void fetchFull(eProp.seq).then(setFull).catch((error) => setReadError(String(error)))
      .finally(() => setLoadingFull(false));
  };
  const displayElided = needsDisplayRecord(e);
  useEffect(() => {
    if (displayElided && !readError) ensureFull();
  }, [displayElided, eProp.seq]);
  const raw = full ? <RawEvent line={full.line} />
    : readError ? <p role="alert" className="text-sm text-destructive">{readError} <button onClick={ensureFull}>retry</button></p>
    : <p className="text-xs text-muted-foreground">{fetchFull ? "Loading disk line…" : "Disk line unavailable."}</p>;
  let s = STYLES[e.event.type] ?? FALLBACK;
  let summary: ReactNode = null;
  let payload: ReactNode = null;
  let renderer = e.event.type;
  const hint = renderHint(e.event, definitions);
  const quiet = e.event.type === "status";
  if (e.event.type === "log" && e.event.level === "error") s = { ...s, icon: RED_ICON };
  if (e.event.type === "log" && e.event.level === "warn") s = { ...s, icon: "text-amber-600 dark:text-amber-400" };

  if (displayElided) {
    summary = <span className="text-xs text-muted-foreground">{e.event.kind ?? e.event.type} · loading full record…</span>;
  } else if (e.event.type === "llm.call") {
    const u = e.event.output?.usage ?? {};
    const resp = e.event.output?.choices?.[0]?.message;
    const respCalls: Record<string, any>[] = Array.isArray(resp?.tool_calls) ? resp.tool_calls : [];
    const { text: content, reasoning, redacted } = splitContent(resp);
    /* reasoning, message text, and tool calls are THREE independent optional
       components; a turn is only "empty" when ALL are absent (~0 output tokens —
       and a redacted-only turn is thinking we can't read, not a dead turn) */
    const emptyResp = !e.event.error && !content.trim() && !reasoning.trim() && !redacted
      && !respCalls.length && (u.output_tokens === 0 || u.output_tokens === undefined);
    if (e.event.error) s = { ...s, badge: RED, icon: RED_ICON };
    const toolSum = toolsSummary(respCalls.map((tc) => ({
      name: toolName(tc),
      primary: primaryArg(toolName(tc), tc.function?.arguments ?? tc.arguments ?? tc.args),
    })));
    summary = (
      <>
        {profile.showAgent && e.event.agent && <Actor id={e.event.agent} labels={profile.labels} />}
        {e.event.error && (
          <span className={cn("rounded px-1 font-mono text-[10px]", RED)}>{typeof e.event.error === "string" ? "error" : e.event.error.kind}</span>
        )}
        {emptyResp && (
          <span className={EMPTY_FLAG} title="no reasoning, no text, no tool calls, no output tokens — a dead model turn">
            empty response
          </span>
        )}
        {/* collapsed-row preview: first ~2 clamped lines of whichever component
            exists, below the actor on phones and inline on desktop.
            Hidden once the row is open. */}
        {(content.trim() || reasoning.trim() || toolSum) && (
          <span className={cn(
            "line-clamp-2 min-w-0 max-md:basis-full md:flex-1 whitespace-pre-wrap text-xs group-open/row:hidden",
            content.trim() ? "text-muted-foreground"
              : reasoning.trim() ? "italic text-muted-foreground"
              : "font-mono text-[11px] text-muted-foreground",
          )}>
            {content.trim() ? content : reasoning.trim() ? `reasoning · ${reasoning.length} characters` : `→ ${toolSum}`}
          </span>
        )}
        {/* open-row header: the preview above hides, the per-call facts take its
            place — the header stays informative instead of going empty */}
        <span className="hidden min-w-0 flex-1 truncate font-mono text-[11px] text-muted-foreground group-open/row:inline">
          {factsStr(e)}
        </span>
      </>
    );
    payload = <LLMCallMessages record={e} previous={context.previousCalls.get(e.seq)}
      active={expanded || !!diskRecord} loadRecord={fetchFull} />;

  } else if (e.event.type === "run.start") {
    summary = (
      <span className="text-muted-foreground">
        <b className="text-foreground">{e.experiment}</b> · seed {fmtVal(e.event.seed)}
      </span>
    );
    payload = <RunStartCard record={e} summaryHref={context.summaryHref} paramDeclarations={context.params} />;
  } else if (e.event.type === "run.end") {
    if (e.event.state !== "completed") s = { ...s, badge: RED, icon: RED_ICON };
    summary = (
      <>
        <StateBadge state={e.event.state} className="text-[10px]" />
        <span className="text-muted-foreground">
          {duration(e.event.duration_s)} · {exitLabel(e.event.exit_code, context.platform)}
        </span>
      </>
    );
    payload = <RunEndFacts record={e} platform={context.platform} />;
  } else if (e.event.type === "log") {
    const colors: Record<string, string> = {
      debug: "bg-muted text-muted-foreground", info: "bg-blue-500/10 text-blue-700 dark:text-blue-400",
      warn: "bg-amber-500/15 text-amber-700 dark:text-amber-400", error: RED,
    };
    summary = <><span className={cn("shrink-0 rounded px-1.5 text-[10px]", colors[e.event.level])}>{e.event.level}</span>
      <span className="line-clamp-2 whitespace-pre-wrap text-sm group-open/row:line-clamp-none">{e.event.message}</span></>;
  } else if (e.event.type === "status") {
    summary = <><span className="whitespace-pre-wrap text-xs text-muted-foreground">{e.event.detail}</span>
      {e.seq === context.latestStatus && <span data-latest-status="" className="rounded border px-1 text-[10px] text-muted-foreground">latest</span>}</>;
  } else if (e.event.type === "result") {
    summary = <SharedResult name={e.event.name} value={e.event.value}
      definition={context.results.find((result) => result.name === e.event.name)} />;
  } else if (e.event.type === "stdout" || e.event.type === "stderr") {
    summary = <><span className="shrink-0 rounded border px-1.5 text-[10px] text-muted-foreground">{e.event.type}</span>
      <span className="line-clamp-2 whitespace-pre-wrap font-mono text-xs group-open/row:line-clamp-none">{stripAnsi(e.event.line)}</span></>;
  } else if (hint) {
    renderer = "hint";
    summary = <HintedSummary e={e} hint={hint} labels={profile.labels} />;
    const body = hintValue(e.event, hint.body);
    payload = <>
      {body !== undefined && body !== null && (hint.format === "markdown" ? <MdView src={hintText(body)} />
        : hint.format === "json" ? <JsonPre src={JSON.stringify(body, null, 2)} />
        : <p className="whitespace-pre-wrap text-sm [overflow-wrap:anywhere]">{hintBody(e.event, hint)}</p>)}
      <HintFields e={e} hint={hint} />
    </>;
  } else {
    renderer = "raw";
    summary = <span className="font-mono text-xs text-muted-foreground">{e.event.kind ?? e.event.type}</span>;
    // Without a declared renderer, opening the row shows its exact disk line.
    payload = null;
  }

  /* everything stripped from the header stays discoverable here */
  const rowTitle = `${e.event.type}${e.event.kind ? ` · ${e.event.kind}` : ""} · seq ${fmtVal(e.seq)}`
    + `${e.event.via ? ` · via ${String(e.event.via)}` : ""}`;
  return (
    <details
      id={`ev-${String(e.seq)}`}
      data-event-renderer={renderer}
      className="group/row border-b border-border/60 last:border-b-0"
      onToggle={(event) => {
        if (event.target !== event.currentTarget) return;
        setExpanded(event.currentTarget.open);
        if (event.currentTarget.open) ensureFull();
      }}
    >
      <summary
        title={rowTitle}
        className={cn(
        "flex cursor-pointer items-baseline gap-2 px-2 hover:bg-muted/40 [&::-webkit-details-marker]:hidden",
        /* open rows: keep the header at least two text rows tall — a thin
           near-empty strip reads as broken and is a poor collapse target */
        "group-open/row:min-h-12",
        quiet ? "py-0.5" : "py-1",
      )}>
        <Gutter g={gutter} />
        <HintIcon hint={hint} fallback={s.Icon} className={cn("shrink-0 self-center", s.icon, quiet ? "size-3 opacity-60" : "size-3.5")} />
        <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-2 text-sm">{summary}</span>
      </summary>
      <div className="space-y-1.5 py-1.5 pl-3 pr-3 md:pl-9">
        {payload}
        <details data-raw-disclosure="" onToggle={(event) => { if (event.currentTarget.open) ensureFull(); }}>
          <summary className="cursor-pointer text-[10px] uppercase tracking-wider text-muted-foreground">raw event</summary>
          {raw}
        </details>
      </div>
    </details>
  );
}

/* ---------------- the pane ---------------- */

export function EventStream({ events, state, cid, rid, definitions = [], visibleEvents, review = false, facets, paramDeclarations, diskRecords }: {
  events: Ev[]; state: string; cid?: string; rid?: string; definitions?: EventDefinition[]; visibleEvents?: Ev[]; review?: boolean; facets?: ReactNode;
  paramDeclarations?: Record<string, ParamDecl>;
  /** Offline review can supply the same record + original line as the endpoint. */
  diskRecords?: ReadonlyMap<number, FullEvent>;
}) {
  const paneRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const [following, setFollowing] = useState(true);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewH, setViewH] = useState(600);

  /* auto-follow while the user hasn't scrolled up */
  useEffect(() => {
    const el = paneRef.current;
    if (el && followRef.current) el.scrollTop = el.scrollHeight;
  }, [events.length, visibleEvents?.length]);
  useEffect(() => {
    const el = paneRef.current;
    if (!el) return;
    const resize = () => setViewH(el.clientHeight || 600);
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const onScroll = () => {
    const el = paneRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    followRef.current = atBottom;
    setFollowing(atBottom);
    setScrollTop(el.scrollTop);
    if (el.clientHeight && el.clientHeight !== viewH) setViewH(el.clientHeight);
  };
  const jump = () => {
    const el = paneRef.current;
    if (!el) return;
    followRef.current = true;
    setFollowing(true);
    el.scrollTop = el.scrollHeight;
    setScrollTop(el.scrollTop);
  };

  const live = state === "running";
  // Stable loader; fetchFullEvent shares and caches the full disk-line request.
  const fetchFull = useMemo(() => cid && rid
    ? (seq: number) => fetchFullEvent(cid, rid, seq) : undefined, [cid, rid]);
  const history = useMemo(() => previousCalls(events), [events]);

  const shown = visibleEvents ?? events;
  const profile = deriveProfile(events, definitions);
  const startRecord = events.find((e) => e.event.type === "run.start");
  const [fullStart, setFullStart] = useState<FullEvent | null>(null);
  useEffect(() => {
    if (!startRecord || !cid || !rid || !containsElision(startRecord.event.result_definitions)) return;
    let active = true;
    void fetchFullEvent(cid, rid, startRecord.seq).then((full) => { if (active) setFullStart(full); })
      .catch(() => { /* The start row exposes a retry and the read error. */ });
    return () => { active = false; };
  }, [cid, rid, startRecord]);
  const startFacts = fullStart?.record.run === startRecord?.run ? fullStart?.record.event : startRecord?.event;
  const context: SharedContext = {
    previousCalls: history,
    results: startFacts?.result_definitions ?? [],
    latestStatus: events.filter((e) => e.event.type === "status").at(-1)?.seq,
    platform: startFacts?.runtime?.platform,
    summaryHref: runHref(cid ?? startFacts?.condition ?? "", rid ?? startRecord?.run ?? "", { tab: "summary", filter: null }),
    params: paramDeclarations,
  };
  /* user toggle wins (persisted for the session); the profile supplies the
     default — the future manifest hint lands there */
  const [gModePick, setGModePick] = useState<GutterMode | null>(uiState.gutterMode ?? null);
  const gutterMode = gModePick ?? profile.gutterMode;
  const setGutterMode = (m: string) => {
    uiState.gutterMode = m as GutterMode;
    setGModePick(m as GutterMode);
  };
  const gutters = buildGutters(shown, gutterMode, events[0]?.ts);
  /* ---------- windowed rendering (hand-rolled, no dependency) ----------
     A 5000-event stream must not build 5000 DOM nodes: above the threshold we
     render only the rows near the viewport between two spacer divs sized by a
     row-height estimate. Auto-follow keeps working: scrolling to the
     (spacer-inflated) bottom updates scrollTop, which selects the tail window. */
  const VIRT_AT = 200;
  const EST = 34;
  const virt = shown.length > VIRT_AT && !review;
  const OVERSCAN = 30;
  const start = virt ? Math.max(0, Math.floor(scrollTop / EST) - OVERSCAN) : 0;
  const end = virt
    ? Math.min(shown.length, Math.ceil((scrollTop + viewH) / EST) + OVERSCAN)
    : shown.length;
  const windowed = shown.slice(start, end);

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border bg-card">
      {/* pane header: the liveness dot sits HERE, next to the stream it vouches
          for, adjacent to the auto-follow control */}
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b bg-muted/30 px-2.5 py-1">
        <LiveDot state={state} />
        <span className="font-mono text-[11px] text-muted-foreground">
          {shown.length !== events.length ? `${shown.length}/${events.length}` : events.length} events
        </span>
        <span className="ml-auto flex items-center gap-2.5">
          <Segmented options={["absolute", "relative"]} value={gutterMode} onChange={setGutterMode} />
          {following ? (
            <span className="text-[11px] text-muted-foreground">
              {live ? "following — auto-scrolls on new events" : "at end"}
            </span>
          ) : (
            <button
              type="button"
              onClick={jump}
              className="inline-flex items-center gap-1.5 rounded-full border bg-background px-2.5 py-0.5 text-[11px] hover:bg-accent"
            >
              <ArrowDown className="size-3" />
              {live ? "following paused — jump to latest" : "jump to latest"}
            </button>
          )}
        </span>
      </div>
      {facets}
      <div ref={paneRef} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
        {virt && start > 0 && <div style={{ height: start * EST }} aria-hidden />}
        {windowed.map((e) => <Row key={e.seq} e={e} definitions={definitions}
          profile={profile} gutter={gutters.get(e.seq)} fetchFull={fetchFull} context={context} diskRecord={diskRecords?.get(e.seq)} />)}
        {virt && end < shown.length && <div style={{ height: (shown.length - end) * EST }} aria-hidden />}
        {!shown.length && (
          <p className="p-3 text-sm text-muted-foreground">
            {events.length ? "no events match this filter" : "no events yet"}
          </p>
        )}
      </div>
    </div>
  );
}
