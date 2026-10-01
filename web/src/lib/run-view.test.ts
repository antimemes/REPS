import { envelope } from "../../test/event-fixtures.ts";
import assert from "node:assert/strict";
import test from "node:test";
import { compileSchemas } from "./render-hints.ts";
import { hashRoute, matchesRunFilter, readRunTab, readRunView, rememberRunTab, runActivity,
  runHref, selectRunTab, type StreamFilter } from "./run-view.ts";

const definitions = compileSchemas([{ oneOf: [
  { properties: { type: { const: "llm.call" } }, "x-adb-render": { actor: "agent" } },
  { properties: { type: { const: "custom" }, kind: { const: "example.speech" } },
    "x-adb-render": { actor: "data.id", actor_label: "data.name" } },
] }]);

test("run tab and facet round-trip in the hash URL without becoming route IDs", () => {
  const filters: StreamFilter[] = [null, { by: "kind", value: "llm.call" },
    { by: "namespace", value: "example" },
    { by: "actor", value: "framework/summarize: a+b & c? #ü" }];
  for (const tab of ["summary", "stream"] as const) for (const filter of filters) {
    const href = runHref("condition", "run", { tab, filter });
    const route = hashRoute(href);
    assert.deepEqual(route.parts, ["run", "condition", "run"]);
    assert.deepEqual(readRunView(route.query), { tab, filter });
    assert.equal(runHref("condition", "run", readRunView(route.query)), href);
    // The bare run resolver preserves the same query when adding a condition.
    const bare = hashRoute(`#/runs/run?${route.query}`);
    assert.deepEqual(bare.parts, ["runs", "run"]);
    assert.deepEqual(readRunView(bare.query), { tab, filter });
  }
  assert.deepEqual(readRunView("tab=invalid&filter=unknown:x"), { tab: null, filter: null });
  assert.equal(readRunView("filter=actor:").filter, null);
});

test("namespace filters match the first dotted segment, leaving undotted kinds independent", () => {
  const events = [
    { type: "custom", kind: "example.speech" }, { type: "custom", kind: "example.nested.kind" },
    { type: "custom", kind: "example2.speech" }, { type: "custom", kind: "example" },
    { type: "status" }, { type: "run.start" },
  ].map((event, seq) => envelope(event, seq));
  assert.deepEqual(events.filter((event) => matchesRunFilter(event, { by: "namespace", value: "example" }, definitions)), events.slice(0, 2));
  assert.deepEqual(events.filter((event) => matchesRunFilter(event, { by: "kind", value: "example" }, definitions)), [events[3]]);
  assert.deepEqual(events.filter((event) => matchesRunFilter(event, { by: "kind", value: "status" }, definitions)), [events[4]]);
});

test("terminal defaults, explicit URL choices and browser preferences have stable precedence", () => {
  const data = new Map<string, string>();
  const storage = { getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => { data.set(key, value); } };
  const implicit = readRunView("");
  assert.equal(selectRunTab(implicit, readRunTab(storage), false), "stream");
  assert.equal(selectRunTab(implicit, readRunTab(storage), true), "summary");
  rememberRunTab(storage, "stream");
  assert.equal(selectRunTab(implicit, readRunTab(storage), true), "stream");
  assert.equal(selectRunTab(readRunView("tab=summary"), readRunTab(storage), false), "summary");
  rememberRunTab(storage, "summary");
  assert.equal(selectRunTab(implicit, readRunTab(storage), false), "summary");
  const unavailable = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  assert.equal(readRunTab(unavailable), null);
  assert.doesNotThrow(() => rememberRunTab(unavailable, "stream"));
});

test("activity counts and actor/kind links select the same events, including unknown kinds", () => {
  const events = [
    { type: "llm.call", agent: "a" }, { type: "llm.call", agent: "b" },
    { type: "custom", kind: "example.speech", data: { id: "a", name: "Alex" } },
    { type: "custom", kind: "future.kind", agent: "not hinted" },
  ].map((event, seq) => envelope(event, seq));
  const activity = runActivity(events, definitions);
  assert.deepEqual(activity.actors, [{ id: "a", label: "Alex", count: 2 }, { id: "b", label: "b", count: 1 }]);
  const entries = [...activity.kinds].map(([value, count]) => ({ filter: { by: "kind", value } as StreamFilter, count }))
    .concat(activity.actors.map(({ id, count }) => ({ filter: { by: "actor", value: id }, count })));
  for (const { filter, count } of entries) {
    const options = readRunView(hashRoute(runHref("c", "r", { tab: "stream", filter })).query);
    assert.equal(selectRunTab(options, "summary", true), "stream");
    assert.equal(events.filter((e) => matchesRunFilter(e, options.filter, definitions)).length, count);
  }
  assert.equal(events.filter((e) => matchesRunFilter(e, { by: "kind", value: "absent" }, definitions)).length, 0);
  assert.deepEqual(events.filter((e) => matchesRunFilter(e, { by: "kind", value: "custom" }, definitions)), [events[3]]);
});
