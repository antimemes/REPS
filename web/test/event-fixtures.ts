import type { Ev, EventPayload, RunCard, RunDerived } from "../src/shared/types.ts";
import { cardMeta } from "../src/lib/run-readability.ts";

export const envelope = (event: EventPayload, seq = 0, ts = "2026-09-16T12:00:00.000000Z"): Ev => ({
  v: 0, ts, run: "20260916t120000z-012345abcdef", experiment: "example", schema: 0, seq, event,
});

/** Test card scaffolding; totals are supplied explicitly, as they are by the runner. */
export function fixtureCard(events: Ev[], derived: Partial<RunDerived> = {}): RunCard {
  const first = events[0] ?? envelope({ type: "run.start" });
  const start: EventPayload = events.find(e => e.event.type === "run.start")?.event ?? { type: "run.start" };
  const end = events.find(e => e.event.type === "run.end");
  const last = events.at(-1) ?? first;
  return {
    identity: { run: first.run, condition: start.condition ?? "condition", experiment: first.experiment, schema: first.schema },
    inputs: { params: start.params ?? {}, seed: start.seed ?? 1 },
    provenance: { source: start.source ?? "test-source", runtime: start.runtime ?? {},
      ...(start.fetch_ref !== undefined ? { fetch_ref: start.fetch_ref } : {}),
      ...(start.tree_hash !== undefined ? { tree_hash: start.tree_hash } : {}) },
    definitions: { results: start.result_definitions ?? [] },
    lifecycle: { state: end?.event.state ?? "running", started_at: first.ts,
      ...(end ? { finished_at: end.ts, duration_s: end.event.duration_s ?? 0, exit_code: end.event.exit_code ?? 0 } : {}) },
    derived: { results: {}, served_models: [],
      usage: { input_tokens: 0, output_tokens: 0 },
      counts: { llm_calls: 0, failed_calls: 0, by_kind: {}, llm_calls_by_agent: {} },
      last_seq: last.seq, last_event_at: last.ts,
      ...(events.some(e => e.event.type === "status") ? { last_status: [...events].reverse().find(e => e.event.type === "status")!.event.detail } : {}),
      ...derived },
  };
}
export const fixtureMeta = (events: Ev[], derived: Partial<RunDerived> = {}) =>
  ({ ...cardMeta(fixtureCard(events, derived)), heartbeat_at: events.at(-1)?.ts });
