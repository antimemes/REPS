/* Run navigation and activity links for recorded events. */
import type { Ev } from "../shared/types.ts";
import { actorFor, actorLabels, eventKind, kindNamespace, schemaKinds, type EventDefinition } from "./render-hints.ts";

export type RunTab = "summary" | "stream";
export type StreamFilter = { by: "kind" | "namespace" | "actor"; value: string } | null;
export interface RunViewOptions { tab: RunTab | null; filter: StreamFilter }
const TAB_KEY = "adb.run.tab";
const asTab = (value: unknown): RunTab | null => value === "summary" || value === "stream" ? value : null;

export function hashRoute(hash: string): { parts: string[]; query: string } {
  const [path = "", query = ""] = hash.replace(/^#/, "").split("?", 2);
  return { parts: path.split("/").filter(Boolean), query };
}

export function readRunView(query: string): RunViewOptions {
  const params = new URLSearchParams(query);
  const raw = params.get("filter") ?? "";
  const colon = raw.indexOf(":");
  const by = raw.slice(0, colon);
  const value = raw.slice(colon + 1);
  return { tab: asTab(params.get("tab")),
    filter: colon > 0 && value && (by === "kind" || by === "namespace" || by === "actor") ? { by, value } : null };
}

export function runHref(cid: string, rid: string, options: RunViewOptions): string {
  const query = new URLSearchParams();
  if (options.tab) query.set("tab", options.tab);
  if (options.filter) query.set("filter", `${options.filter.by}:${options.filter.value}`);
  return `#/run/${encodeURIComponent(cid)}/${encodeURIComponent(rid)}${query.size ? `?${query}` : ""}`;
}

export function readRunTab(storage: Pick<Storage, "getItem">): RunTab | null {
  try { return asTab(storage.getItem(TAB_KEY)); } catch { return null; }
}

export function rememberRunTab(storage: Pick<Storage, "setItem">, tab: RunTab): void {
  try { storage.setItem(TAB_KEY, tab); } catch { /* URL still retains the choice. */ }
}

export function selectRunTab(options: RunViewOptions, preferred: RunTab | null, ended: boolean): RunTab {
  return options.tab ?? preferred ?? (ended ? "summary" : "stream");
}

export function matchesRunFilter(event: Ev, filter: StreamFilter, definitions: EventDefinition[]): boolean {
  if (!filter) return true;
  if (filter.by === "actor") return actorFor(event, definitions) === filter.value;
  if (filter.by === "namespace") return kindNamespace(eventKind(event)) === filter.value;
  return eventKind(event) === filter.value || (filter.value === "custom" && event.event.type === "custom"
    && !schemaKinds(definitions).includes(event.event.kind ?? "custom"));
}

export function runActivity(events: Ev[], definitions: EventDefinition[]) {
  const kinds = new Map<string, number>();
  const actors = new Map<string, number>();
  const labels = actorLabels(events, definitions);
  for (const event of events) {
    const kind = eventKind(event);
    kinds.set(kind, (kinds.get(kind) ?? 0) + 1);
    const actor = actorFor(event, definitions);
    if (actor !== null) actors.set(actor, (actors.get(actor) ?? 0) + 1);
  }
  return { kinds, actors: [...actors].map(([id, count]) => ({ id, label: labels.get(id) ?? id, count })) };
}

export function elapsedSeconds(start: unknown, now: number): number | null {
  const ms = typeof start === "number" ? (start < 1e12 ? start * 1000 : start)
    : typeof start === "string" ? Date.parse(start) : NaN;
  return Number.isFinite(ms) ? Math.max(0, (now - ms) / 1000) : null;
}

export function duration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  if (seconds < 60) return `${Number(seconds.toFixed(1))}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${Math.floor(seconds % 60)}s`
    : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}
