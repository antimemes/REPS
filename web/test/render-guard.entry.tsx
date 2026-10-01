/* Render guards over saved GovSim envelopes plus focused model-content fixtures. */

// minimal browser shims for module-scope references (lib/data reads
// window.location at import time)
(globalThis as Record<string, unknown>).window = {
  location: { pathname: "/", hash: "#/" },
  addEventListener: () => {},
};
(globalThis as Record<string, unknown>).localStorage = {
  getItem: () => null,
  setItem: () => {},
};
(globalThis as Record<string, unknown>).document = {
  documentElement: { classList: { contains: () => false } },
};

import { readFileSync } from "node:fs";
import { mkdtemp, mkdir, writeFile, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { EventStream, RawEvent, deriveProfile } from "../src/components/event-stream";
import { RunView } from "../src/pages/run";
import { RunsTable } from "../src/pages/runs";
import { ConditionView } from "../src/pages/condition";
import { OverviewView } from "../src/pages/overview";
import { RunReader } from "../src/server/runs";
import { copyBadRunCorpus, badCardNames, badRunNames } from "./bad-run-corpus";
import { hashRoute } from "../src/lib/run-view";
import { jsonTokens } from "../src/lib/json-text";
import { ExperimentReadme } from "../src/pages/experiment";
import { GetData } from "../src/components/get-data";
import { dataSource, setDataSource } from "../src/lib/data-source";
import { RunDataContext, VegaChart } from "../src/components/vega-chart";
import { AggChips, ResultChips, ResultFacts, ResultRows, ExperimentResults, InstanceScoreChips } from "../src/components/results";
import type { ResultDecl } from "../src/shared/types";
import { parseEnvelope, parseEventLine } from "../src/lib/envelope";
import { envelope, fixtureMeta } from "./event-fixtures";
import type { Ev } from "../src/shared/types";

import { compileSchemas, renderHint, hintBody, hintValue, hintText } from "../src/lib/render-hints";
import { readRunSchemas } from "../src/server/event-schemas";
const sharedSchema = JSON.parse(readFileSync("../lib/adb-events/adb_events/schema.json", "utf8"));
const govsimSchema = JSON.parse(readFileSync("test/fixtures/govsim-schema.json", "utf8"));
const govsimDefinitions = compileSchemas([sharedSchema, govsimSchema]);
if (process.env.ADB_TEST_MANIFESTS) {
  const manifest = JSON.parse(readFileSync(join(process.env.ADB_TEST_MANIFESTS, "govsim.json"), "utf8"));
  assert.deepEqual(JSON.parse(readFileSync(manifest.schema.path, "utf8")), govsimSchema,
    "Refresh the GovSim schema fixture from the built export when hints change");
}
const hintDefinitions = compileSchemas([sharedSchema, {
  oneOf: [{ properties: { type: { const: "custom" }, kind: { const: "test.message" } },
    "x-adb-render": { icon: "message-circle", actor: "data.speaker", title: "data.speaker", body: "data.text", format: "markdown" } }],
}]);

const raw = readFileSync(process.env.FIXTURE!, "utf8"); // absolute path from render-check.mjs
const events: Ev[] = raw.split("\n").filter(Boolean).map((l) => parseEventLine(l));

const modelRun = fixtureMeta(events);
const comparisonRuns = [
  { condition: "base", experiment: "govsim", source: "same", params: { threads: 2, model: "a" } },
  { condition: "sibling", experiment: "govsim", source: "same", params: { threads: 3, model: "a" } },
  { condition: "two-differences", experiment: "govsim", source: "same", params: { threads: 3, model: "b" } },
  { condition: "old", experiment: "govsim", source: "old", params: { threads: 3, model: "a" } },
].map((c) => ({ ...modelRun, ...c, run: `${c.condition}-run` }));
const conditionHtml = renderToStaticMarkup(<ConditionView cid="base" runs={comparisonRuns} />);
assert.match(conditionHtml, /threads.*2.*→.*3/);
assert.match(conditionHtml, /#\/conditions\/base\?pool=threads/);
assert.ok(!conditionHtml.includes("two-differences-run"));
const pooledHtml = renderToStaticMarkup(<ConditionView cid="base" query="pool=threads" runs={comparisonRuns} />);
assert.match(pooledHtml, /Pooled runs · varying threads/);
assert.match(pooledHtml, /base-run/);
assert.match(pooledHtml, /sibling-run/);
assert.ok(!pooledHtml.includes("old-run") && !pooledHtml.includes("two-differences-run"));
modelRun.params = { model: "azure/gpt-5-nano" };
modelRun.derived!.served_models = ["gpt-5-nano-2025-08-07"];
const servedList = renderToStaticMarkup(<RunsTable runs={[modelRun]} />);
assert.ok(servedList.includes("gpt-5-nano") && servedList.includes("served: gpt-5-nano-2025-08-07"),
  "A single condition still shows the requested model beside its served snapshot");
modelRun.derived!.served_models = ["gpt-5-nano"];
assert.ok(!renderToStaticMarkup(<RunsTable runs={[modelRun]} />).includes("served:"),
  "Matching model names do not add duplicate labels");

const html = renderToStaticMarkup(
  <EventStream review events={events} definitions={govsimDefinitions} state="completed" cid="fixturecid" rid="FIXTURERID" />,
);

const leaks = ["[object Object]", "undefined,undefined", "NaN undefined"]
  .filter((s) => html.includes(s));
if (leaks.length) {
  console.error(`render guard FAILED: markup contains ${leaks.join(", ")}`);
  process.exit(1);
}
if (events.length < 10) {
  console.error("render guard FAILED: fixture suspiciously small");
  process.exit(1);
}
console.log(`render guard ok — ${events.length} real events, ${html.length} chars of markup, no coercion leaks`);

// The same generic row handles any declared kind, with no producer branches.
const messages = [0, 1].map((seq) => envelope({ type: "custom", kind: "test.message",
  data: { speaker: "Mayor", text: "Take **two**. <script>unsafe</script>" } }, seq,
  `2026-09-15T12:00:00.12345${seq}Z`));
const rendered = renderToStaticMarkup(<EventStream events={messages} definitions={hintDefinitions}
  state="completed" cid="hints" rid="open" />);
assert.match(rendered, /<b>two<\/b>/);
assert.match(rendered, /lucide-message-circle/);
assert.match(rendered, /id="ev-1"/);
assert.ok(!rendered.includes("<script>"));
assert.ok(!rendered.includes("[object Object]"));
assert.deepEqual(deriveProfile(messages, hintDefinitions).agents.map((a) => a.name), ["Mayor"]);
assert.equal(deriveProfile(messages, hintDefinitions).agentCount, 1);
assert.equal(deriveProfile(messages, hintDefinitions).showAgent, false);
assert.match(rendered, /from run start/);
assert.ok(rendered.indexOf('id="ev-0"') < rendered.indexOf('id="ev-1"'));
assert.ok(!rendered.includes("data-event-group"));
assert.ok(!rendered.includes("data-group-tag"));

// Historical run bytes carry identity, not schema files. Current build hints
// label earlier calls even when the later replay is hidden by a facet filter.
async function historicalRunWithCurrentHints() {
  const root = await mkdtemp(join(tmpdir(), "adb-render-history-"));
  try {
    const run = join(root, "old-run");
    const build = join(root, "current-build");
    await Promise.all([mkdir(run), mkdir(build)]);
    const schema = { oneOf: [{ properties: { type: { const: "custom" }, kind: { const: "test.message" } },
      "x-adb-render": { icon: "message-circle", actor: "data.id", actor_label: "data.name", body: "data.text", format: "markdown" } }] };
    const payloads = [
      { type: "llm.call", agent: "persona_0", output: { choices: [{ message: { content: "A model reply" } }] } },
      { type: "llm.call", agent: "framework" },
      { type: "custom", kind: "test.message", data: { id: "persona_0", name: "John", text: "Current **hint**" } },
      { type: "custom", kind: "test.message", data: { id: "framework", name: "Mayor", text: "Mayor line" } },
      { type: "custom", kind: "test.message", data: { id: "framework", name: "framework", text: "Summary line" } },
    ];
    await Promise.all([
      writeFile(join(run, "events.jsonl"), payloads.map((event, seq) => JSON.stringify({
        v: 0, run: "20260916t120000z-012345abcdef", experiment: "example", schema: 0, seq, ts: "2026-09-14T12:00:00.000000Z", event,
      })).join("\n")),
      writeFile(join(build, "schema.json"), JSON.stringify(schema)),
      writeFile(join(build, "shared-schema.json"), JSON.stringify(sharedSchema)),
    ]);
    assert.deepEqual(await readdir(run), ["events.jsonl"]);
    const records = (await readFile(join(run, "events.jsonl"), "utf8")).split("\n").map((line) => JSON.parse(line));
    const definitions = compileSchemas(await readRunSchemas([
      { name: "example", params: {}, schema: { version: 0, models: "example:Payload", path: join(build, "schema.json") } },
    ], records[0].experiment, records[0].schema));
    const events = records.map(parseEnvelope);
    const profile = deriveProfile(events, definitions);
    assert.deepEqual(profile.agents.map((a) => [a.id, a.name]), [["persona_0", "John"], ["framework", "framework"]]);
    assert.equal(profile.agentCount, 2);
    assert.equal(profile.showAgent, true);
    const html = renderToStaticMarkup(<EventStream review events={events} definitions={definitions} state="completed" />);
    assert.match(html, /Current <b>hint<\/b>/);
    assert.match(html, /title="persona_0">John<\/span>/);
    assert.match(html, /title="framework">framework<\/span>/);
    assert.match(html, /title="framework">Mayor<\/span>/); // The row's own label wins over propagated names.
    const filtered = renderToStaticMarkup(<EventStream events={events} visibleEvents={events.slice(0, 2)}
      definitions={definitions} state="completed" />);
    assert.match(filtered, /title="persona_0">John<\/span>/);
    const group = renderToStaticMarkup(<EventStream events={events} visibleEvents={events.slice(2)}
      definitions={definitions} state="completed" />);
    assert.match(group, /title="persona_0">John<\/span>/);
    console.log("historical run renders with current build hints, row labels, and actor ID hovers");
  } finally { await rm(root, { recursive: true }); }
}
historicalRunWithCurrentHints().catch((error) => { console.error(error); process.exitCode = 1; });

// One speaking persona supplies a row label; every silent persona is named from
// the config roster, including calls before the config and a calls-only filter.
const rosterDefinitions = compileSchemas([sharedSchema, { oneOf: [
  { properties: { type: { const: "custom" }, kind: { const: "govsim.config" } },
    "x-adb-render": { icon: "settings", actor_registry: { path: "data.experiment.personas", label: "name" } } },
  { properties: { type: { const: "custom" }, kind: { const: "govsim.utterance" } },
    "x-adb-render": { icon: "message-circle", actor: "data.agent_id", actor_label: "data.agent_name", body: "data.utterance" } },
] }]);
const roster = ["John", "Kate", "Jack", "Emma", "Luke"];
const rosterEvents = [
  ...roster.map((_, i) => ({ type: "llm.call", agent: `persona_${i}` })),
  { type: "custom", kind: "govsim.config", data: { experiment: {
    personas: { ...Object.fromEntries(roster.map((name, i) => [`persona_${i}`, { name }])), num: 5 },
  } } },
  { type: "custom", kind: "govsim.utterance", data: { agent_id: "persona_0", agent_name: "John", utterance: "Only I speak." } },
].map((event, seq) => envelope(event, seq));
assert.deepEqual(deriveProfile(rosterEvents, rosterDefinitions).agents.map((actor) => actor.name), roster);
const rosterHtml = renderToStaticMarkup(<RunView cid="roster" rid="one-speaker" events={rosterEvents} meta={fixtureMeta(rosterEvents)}
  definitions={rosterDefinitions} query="tab=stream&filter=kind%3Allm.call" />);
for (const [i, name] of roster.entries()) assert.match(rosterHtml, new RegExp(`title="persona_${i}">${name}</span>`));
const rosterSummary = renderToStaticMarkup(<RunView cid="roster" rid="one-speaker" events={rosterEvents} meta={fixtureMeta(rosterEvents)}
  definitions={rosterDefinitions} query="tab=summary" />);
for (const name of roster) assert.ok(rosterSummary.includes(name));

const customHtml = renderToStaticMarkup(<EventStream events={[envelope({
  type: "custom", kind: "example.measurement", data: { nested: { value: 42 } },
})]} state="completed" />);
assert.match(customHtml, /example.measurement/);
assert.match(customHtml, /Disk line unavailable/);
assert.ok(!customHtml.includes("[object Object]"));

// The new ModelEvent data shape renders through the same viewer as historical runs.
const modelData = JSON.parse(readFileSync("../lib/adb-events/tests/fixtures/llm-call.json", "utf8"));
const modelHtml = renderToStaticMarkup(<EventStream events={[envelope(modelData)]} state="completed" />);
assert.match(modelHtml, /Answer/);
assert.match(modelHtml, /lookup/);
assert.match(modelHtml, /A short summary/);
assert.ok(!modelHtml.includes("[object Object]"));
const errorHtml = renderToStaticMarkup(<EventStream events={[envelope({
  ...modelData, error: "connection refused", output: { model: "m", choices: [] },
})]} state="completed" />);
assert.match(errorHtml, /connection refused/);

// Optional catalog documentation uses the real safe renderer, including in old
// manifests that predate the field. Raw HTML must remain inert.
for (const readme of [undefined, "", "  \n"]) {
  assert.equal(renderToStaticMarkup(<ExperimentReadme readme={readme} />), "");
}
const readmeHtml = renderToStaticMarkup(<ExperimentReadme readme={
  '# Study\n\nA **documented** experiment.\n\n- First treatment\n\n'
  + '[Paper](https://example.org/paper)\n\n```sh\nadb-runner --describe\n```\n\n'
  + '<script>alert(1)</script>'
} />);
assert.match(readmeHtml, /<details[^>]* open=""/);
assert.match(readmeHtml, /About this experiment/);
assert.match(readmeHtml, /<h3>Study<\/h3>/);
assert.match(readmeHtml, /<b>documented<\/b>/);
assert.match(readmeHtml, /<li>First treatment<\/li>/);
assert.match(readmeHtml, /href="https:\/\/example.org\/paper"/);
assert.match(readmeHtml, /<pre class="code"><code>adb-runner --describe/);
assert.ok(!readmeHtml.includes('<script>'));
assert.match(readmeHtml, /&lt;script&gt;/);
console.log('experiment README render guard ok — optional, rendered Markdown, inert raw HTML');

const localSource = dataSource();
assert.equal(renderToStaticMarkup(<GetData name="govsim" runCount={245} />), "");
try {
  const noRequest = async (): Promise<never> => { throw new Error("GetData must not fetch"); };
  setDataSource({ mode: "published", pollMs: 0, asset: (path) => `/site/${path}`,
    json: noRequest, text: noRequest, post: noRequest });
  const downloadHtml = renderToStaticMarkup(<GetData name="govsim" runCount={245} />);
  assert.match(downloadHtml, /Get the data/);
  assert.match(downloadHtml, /245 runs · 490 files/);
  assert.match(downloadHtml, /\/site\/index\/experiments\/govsim\/index.jsonl/);
  assert.match(downloadHtml, /read_json_objects/);
  assert.match(downloadHtml, /unnest\(inputs.params\), unnest\(derived.results\)/);
  assert.equal((downloadHtml.match(/<pre /g) ?? []).length, 2);
} finally { setDataSource(localSource); }
console.log("Get the data snippets render in published mode only");

const emptyChart = renderToStaticMarkup(<RunDataContext value={[]}>
  <VegaChart spec={{ data: { name: "runs" }, mark: "point" }} />
</RunDataContext>);
assert.match(emptyChart, /No recorded runs yet\. This figure will appear after a run\./);
assert.ok(!emptyChart.includes("vega-host") && !emptyChart.includes('role="alert"'));
console.log("VegaChart empty state renders without initializing a chart");

const definitions: ResultDecl[] = [
  { name: "collapsed", type: { kind: "bool" }, label: "Resource collapsed", description: "Simulation outcome, not execution failure.", details: "The resource fell below its threshold. <script>unsafe</script>" },
  { name: "harvest", type: { kind: "int" }, label: "Total harvest", unit: "resource units" },
  { name: "absent", type: { kind: "int" }, label: "Not emitted" },
];
const resultHtml = renderToStaticMarkup(<ResultChips definitions={definitions}
  summary={{ collapsed: false, harvest: 50, surprise: 2 }} metrics={[{ name: "harvest", value: 50, unit: "fish" }]} />);
assert.match(resultHtml, /Resource collapsed/);
assert.match(resultHtml, />No<\/b>/);
assert.match(resultHtml, /Total harvest/);
assert.match(resultHtml, />fish<\/span>/);
assert.match(resultHtml, /surprise/);
assert.ok(!resultHtml.includes("Not emitted"));
assert.ok(!/emerald|red-|✓|✗/.test(resultHtml));
const rows = renderToStaticMarkup(<ResultRows definitions={definitions}
  summary={{ collapsed: false, harvest: 50, surprise: 2 }}
  metrics={[{ name: "harvest", value: 10, unit: "kg" }, { name: "harvest", value: 40, unit: "fish" },
    { name: "pool", value: 18 }, { name: "pool", value: 20 }]}
  scores={[{ harvest: 10, refused: false }, { harvest: 30, refused: true }]} />);
assert.equal((rows.match(/data-result-name="harvest"/g) ?? []).length, 1);
assert.equal((rows.match(/data-result-name="pool"/g) ?? []).length, 1);
assert.equal((rows.match(/data-result-name=/g) ?? []).length, 5);
assert.match(rows, />50<\/b>/); // summary wins; latest metric supplies its unit
assert.match(rows, />fish<\/span>/);
assert.ok(!rows.includes(">kg<"));
assert.match(rows, />20<\/b>/); // latest metric value wins
assert.match(rows, /×2/); // repeated metric emission stays visible
assert.match(rows, /mean 20 fish · n=2 instances/); // same-key instance aggregate survives
assert.match(rows, /1\/2 Yes · instances/);
assert.match(rows, /aria-expanded="false"/);
assert.match(rows, /No description provided\./);
assert.ok(!rows.includes("Not emitted"));
assert.ok(!rows.includes("Understanding these results"));
assert.ok(!/<details[^>]* open/.test(rows));
assert.ok(!rows.includes("<script>"));
assert.ok(!rows.includes("unsafe"));
// Descriptions remain visible while optional details are collapsed.
const visibleRows = rows.replace(/<details[\s\S]*?<\/details>/g, "");
assert.match(visibleRows, /Simulation outcome, not execution failure\./);
assert.ok(!visibleRows.includes("The resource fell"));
assert.ok(!visibleRows.includes("Result key:"));
const orderedRows = renderToStaticMarkup(<ResultRows definitions={definitions}
  metrics={[{ name: "pool", value: 3 }, { name: "harvest", value: 5 }, { name: "collapsed", value: false }]} />);
assert.deepEqual([...orderedRows.matchAll(/data-result-name="([^"]+)"/g)].map(m => m[1]), ["collapsed", "harvest", "pool"]);
const rawRow = renderToStaticMarkup(<ResultRows summary={{ pool: 3 }} />);
assert.match(rawRow, /pool/);
assert.match(rawRow, /No description provided\./);
assert.ok(!rawRow.includes("<details")); // nothing useful to expand
assert.equal(renderToStaticMarkup(<ResultRows />), "");
assert.equal(renderToStaticMarkup(<ExperimentResults />), "");
const catalog = renderToStaticMarkup(<ExperimentResults definitions={definitions} />);
assert.match(catalog, /Results this experiment records/);
assert.match(catalog, /Not emitted/);
assert.match(catalog, /Unit: resource units/);
assert.ok(!catalog.includes("text-right")); // catalog rows have no value column
assert.ok(!/<details[^>]* open/.test(catalog));
const scoresHtml = renderToStaticMarkup(<InstanceScoreChips scores={[{ refused: false }, { refused: true }]} />);
assert.ok(!/emerald|red-|✓|✗/.test(scoresHtml));
console.log("result rendering ok — labels, units, neutral booleans, undeclared outputs, unified rows, inline descriptions, optional details, deduplication and catalog");

// The run page keeps summary content out of the full-height stream tab.
const runDefinitions: ResultDecl[] = [
  { name: "score", type: { kind: "int" }, label: "Score", description: "Recorded score.", details: "Details stay inline." },
  { name: "collapsed", type: { kind: "bool" }, label: "Collapsed", description: "Simulation outcome." },
];
const runStart = { type: "run.start", seed: 37,
  source: "source-fingerprint", tree_hash: "sha256-tree", params: { resolved: "actual-input" },
  result_definitions: runDefinitions, runtime: { platform: "Linux",
    runner_python_version: "3.13", endpoints: { openai: "https://api.example.invalid" } } };
const partial = [runStart, { type: "log", level: "info", message: "Starting simulation" },
  { type: "llm.call", agent: "Mayor", output: { choices: [{ message: { content: "Observed reply" } }],
    usage: { input_tokens: 3, output_tokens: 5, input_tokens_cache_read: 4, input_tokens_cache_write: 2 } } },
  { type: "llm.call", agent: "other", error: "offline" },
  { type: "result", name: "score", value: 0 },
  { type: "custom", kind: "test.message", data: { speaker: "Mayor", text: "A custom observation" } },
  { type: "status", detail: "Negotiating round 2" },
].map((event, seq) => envelope(event, seq, `2026-09-16T12:00:0${seq}.000000Z`));
const completed = [...partial,
  envelope({ type: "result", name: "collapsed", value: false }, 7, "2026-09-16T12:00:07.000000Z"),
  envelope({ type: "run.end", state: "completed", duration_s: 8, exit_code: 0 }, 8, "2026-09-16T12:00:08.000000Z")];
const now = Date.parse("2026-09-16T12:00:10Z");
const runMeta = (events: Ev[]) => {
  const ended = events.some(({ event }) => event.type === "run.end");
  return fixtureMeta(events, {
    results: ended ? { score: 0, collapsed: false } : { score: 0 },
    usage: { input_tokens: 9, output_tokens: 5 },
    counts: { llm_calls: 2, failed_calls: 1, llm_calls_by_agent: { Mayor: 1, other: 1 },
      by_kind: { "run.start": 1, log: 1, "llm.call": 2, result: ended ? 2 : 1,
        "test.message": 1, status: 1, ...(ended ? { "run.end": 1 } : {}) } },
  });
};
const renderRun = (events: Ev[], query = "", extra = {}) => renderToStaticMarkup(
  <RunView cid="condition" rid="run" events={events} definitions={hintDefinitions}
    query={query} now={now} {...extra} meta={{ ...runMeta(events), ...(extra as { meta?: object }).meta }} />);
for (const events of [completed, partial]) {
  const summary = renderRun(events, "tab=summary");
  assert.match(summary, /data-run-tab="summary"/);
  assert.ok(!/replicate/i.test(summary));
  assert.match(summary, /title="Recorded score\."/);
  assert.match(summary, /data-result-facts/);
  assert.ok(!summary.includes("Details stay inline"));
  assert.match(summary, /data-result-name="score"/);
  assert.match(summary, /data-result-name="collapsed"/);
  assert.match(summary, /actual-input/);
  assert.match(summary, /source-fingerprint/);
  assert.match(summary, /no pinned revision/);
  assert.match(summary, /sha256-tree/);
  assert.match(summary, /https:\/\/api.example.invalid/);
  assert.match(summary, /1 failed calls/);
  const sections = ["Results", "Activity", "Inputs", "Provenance"].map((label) => summary.indexOf(`aria-label="${label}"`));
  assert.ok(sections.every((at, i) => at >= 0 && (i === 0 || at > sections[i - 1]!)));
  assert.ok(!summary.includes('aria-label="Stream filters"'));
  const stream = renderRun(events, "tab=stream");
  assert.match(stream, /data-run-tab="stream"/);
  assert.ok(!/replicate/i.test(stream));
  assert.match(stream, /aria-label="Stream filters"/);
  const callHeader = stream.split('id="ev-2"')[1]!.split("</summary>")[0]!;
  assert.match(callHeader, /title="Mayor">Mayor<\/span>/); // actor remains in the row header
  assert.ok(!stream.includes('aria-label="Results"'));
  assert.ok(!stream.includes('aria-label="Inputs"'));
  assert.ok(!stream.includes("fullscreen"));
  assert.ok(!stream.includes("fixed inset-3"));
}
assert.match(renderRun(completed), /data-run-tab="summary"/);
assert.match(renderRun(partial), /data-run-tab="stream"/);
assert.match(renderRun(completed, "", { preferredTab: "stream" }), /data-run-tab="stream"/);
const progress = renderRun(partial, "tab=summary");
assert.match(progress, /aria-label="Progress"/);
assert.match(progress, /elapsed 10s/);
assert.match(progress, /Negotiating round 2/);
assert.match(progress, /9\+5 tokens/);
assert.match(progress, /last event 4s ago/);
assert.match(progress, /pending/);
assert.match(renderRun(completed, "tab=summary"), /9\+5 tokens/);
assert.ok(!renderRun(completed, "tab=summary").includes("pending"));
const stale = renderRun(partial, "tab=summary", { meta: { state: "running", heartbeat_at: "2026-09-16T11:59:00Z" } });
assert.match(stale, /possibly interrupted/);
assert.match(stale, /interrupted\?/);
// A terminal card can render a summary before the stream has finished loading.
assert.match(renderRun(partial, "tab=summary", { meta: { state: "completed", heartbeat_at: "2026-09-16T11:59:00Z" } }), /aria-label="Outcome"/);
const noStart = renderRun(partial.slice(1), "tab=summary", { meta: runMeta(partial) });
assert.match(noStart, /data-result-name="collapsed"/);
assert.match(noStart, /actual-input/);
const cached = runMeta(completed);
cached.derived!.results = { score: 12345, collapsed: false };
cached.derived!.counts.llm_calls = 987;
cached.derived!.counts.failed_calls = 321;
cached.derived!.counts.llm_calls_by_agent = { "card-only-agent": 987 };
cached.derived!.usage = { input_tokens: 876, output_tokens: 765 };
// Card totals win over different live/completed records and a stream not yet loaded.
for (const [records, state] of [[partial, "running"], [completed, "completed"], [[], "completed"]] as const) {
  const cachedView = renderRun([...records], "tab=summary", { meta: { ...cached, state } });
  assert.match(cachedView, />12345</);
  assert.match(cachedView, /987 calls/);
  assert.match(cachedView, /321 failed calls/);
  assert.match(cachedView, /876\+765 tokens/);
}
// A zero-failure card stays neutral even if a loaded call has an error.
for (const records of [[], [envelope({ type: "llm.call" })], [envelope({ type: "llm.call", error: null })], partial]) {
  const healthy = { ...cached, derived: { ...cached.derived!, counts: { ...cached.derived!.counts, failed_calls: 0 } } };
  assert.match(renderRun(records, "tab=summary", { meta: healthy }), /class="text-muted-foreground">0 failed calls<\/span>/);
}
// Actor activity comes from loaded records and hints, including custom-only actors.
const customActor = envelope({ type: "custom", kind: "test.message",
  data: { speaker: "Observer", text: "No model calls from this actor" } }, 9);
const actorView = renderRun([...completed, customActor], "tab=summary", { meta: cached });
assert.ok(!actorView.includes("card-only-agent"));
const observerLink = [...actorView.matchAll(/href="([^"]+)"/g)].map(match => match[1]!.replaceAll("&amp;", "&"))
  .find(href => new URLSearchParams(hashRoute(href).query).get("filter") === "actor:Observer");
assert.ok(observerLink);
const observerStream = renderRun([...completed, customActor], hashRoute(observerLink).query, { meta: cached });
assert.match(observerStream, /id="ev-9"/);
assert.ok(!observerStream.includes('id="ev-2"'));
// Follow actual links rendered by the activity section, then inspect the filtered stream.
const activityHtml = renderRun(completed, "tab=summary").split('aria-label="Activity"')[1]!.split("</section>")[0]!;
for (const [filter, activeFacet, expected, absent] of [["kind:llm.call", "namespace:llm", [2, 3], [0, 1, 4, 5, 6, 7, 8]],
  ["actor:Mayor", "actor:Mayor", [2, 5], [0, 1, 3, 4, 6, 7, 8]]] as const) {
  const link = [...activityHtml.matchAll(/href="([^"]+)"/g)].map((match) => match[1]!.replaceAll("&amp;", "&"))
    .find((href) => new URLSearchParams(hashRoute(href).query).get("filter") === filter)!;
  assert.ok(link);
  const destination = renderRun(completed, hashRoute(link).query);
  assert.match(destination, /data-run-tab="stream"/);
  assert.match(destination, new RegExp(`data-filter="${activeFacet}" aria-current="true"`));
  for (const seq of expected) assert.ok(destination.includes(`id="ev-${seq}"`));
  for (const seq of absent) assert.ok(!destination.includes(`id="ev-${seq}"`));
}
console.log("run page guard ok — completed/partial tabs, pending results, progress, provenance, and activity links");

// Only namespaces with multiple kinds need a second row; deep links still filter.
const allKinds = renderRun(completed, "tab=stream");
assert.match(allKinds, /data-filter="namespace:run"/);
assert.match(allKinds, /data-filter="namespace:llm"/);
assert.match(allKinds, /data-filter="kind:status"/);
assert.ok(!allKinds.includes('data-facet-level="kinds"'));
assert.ok(!allKinds.includes('data-filter="kind:llm.call"'));
const actorChips = [...allKinds.matchAll(/data-filter="actor:([^"]+)"[^>]*>([\s\S]*?)<\/a>/g)];
assert.ok(actorChips.length > 0);
for (const [chip, actor, label] of actorChips) {
  assert.ok(chip.includes(`title="actor: ${actor}"`));
  assert.equal(label, actor); // names stand alone; identity stays in the tooltip
}
const namespaced = renderRun(completed, "tab=stream&filter=namespace%3Arun");
assert.match(namespaced, /data-facet-level="kinds" aria-label="run kinds"/);
for (const kind of ["run.start", "run.end"])
  assert.ok(namespaced.includes(`data-filter="kind:${kind}"`));
assert.ok(!namespaced.includes('id="ev-2"'));
for (const filter of ["namespace:llm", "kind:llm.call"]) {
  const singleKind = renderRun(completed, `tab=stream&filter=${encodeURIComponent(filter)}`);
  assert.match(singleKind, /data-filter="namespace:llm" aria-current="true"/);
  assert.ok(!singleKind.includes('data-facet-level="kinds"'));
  for (const seq of [2, 3]) assert.ok(singleKind.includes(`id="ev-${seq}"`));
  assert.ok(!singleKind.includes('id="ev-0"'));
}

const facts = renderToStaticMarkup(<ResultFacts definitions={[
  ...runDefinitions, { name: "rate", type: { kind: "float" }, unit: "fish/month", description: "Monthly harvest" },
  { name: "pending", type: { kind: "int" } }, { name: "survived", type: { kind: "bool" } },
]} summary={{ rate: 1.23456, collapsed: false, score: 0, survived: true, undeclared: 999 }} />);
assert.deepEqual([...facts.matchAll(/data-result-name="([^"]+)"/g)].map((m) => m[1]), ["score", "collapsed", "rate", "pending", "survived"]);
assert.match(facts, />0<\/dd>/);
assert.match(facts, />no<\/dd>/);
assert.match(facts, />yes<\/dd>/);
assert.match(facts, /1\.235<span class="[^"]*text-muted-foreground">fish\/month<\/span>/);
assert.match(facts, /aria-label="pending"[^>]*>—<\/span>/);
assert.match(facts, /title="Monthly harvest"/);
// All result views use declaration order, independently of names and arrival order.
const orderedDefinitions: ResultDecl[] = [
  { name: "zulu", type: { kind: "int" }, label: "Zebra" },
  { name: "alpha", type: { kind: "int" }, label: "Ant" },
  { name: "middle", type: { kind: "int" }, label: "Monkey" },
];
const outOfOrderValues = { middle: 3, alpha: 2, zulu: 1 };
for (const view of [
  <ResultFacts definitions={orderedDefinitions} summary={outOfOrderValues} />,
  <ResultRows definitions={orderedDefinitions} summary={outOfOrderValues} />,
  <ResultRows definitions={orderedDefinitions} catalog />,
  <ResultChips definitions={orderedDefinitions} summary={outOfOrderValues} />,
  <ResultChips definitions={orderedDefinitions} summary={{ middle: 3 }}
    metrics={[{ name: "alpha", value: 2 }, { name: "zulu", value: 1 }]} />,
]) {
  const html = renderToStaticMarkup(view);
  assert.ok(html.indexOf("Zebra") < html.indexOf("Ant"));
  assert.ok(html.indexOf("Ant") < html.indexOf("Monkey"));
}
console.log("namespace facets, roster labels, sequential rows and result facts render correctly");

const htmlText = (markup: string) => markup.replace(/<[^>]*>/g, "")
  .replaceAll("&quot;", '"').replaceAll("&#x27;", "'").replaceAll("&#39;", "'")
  .replaceAll("&lt;", "<").replaceAll("&gt;", ">").replaceAll("&amp;", "&");

// Formatting preserves tokens; the disk-line toggle preserves the original bytes.
async function rawLineRoundTrip() {
  const root = await mkdtemp(join(tmpdir(), "adb-raw-render-"));
  try {
    const line = '  {"event" : {"type":"custom","kind":"clock","ts":"1999-01-01T00:00:00Z","data":{"value":1e0,"text":"<é>&\\u0041"}},"seq":9,"schema":0,"experiment":"example","run":"20260916t120000z-012345abcdef","v":0,"ts":"2026-09-16T12:34:56.123456Z"}\r\n';
    const path = join(root, "events.jsonl");
    await writeFile(path, line);
    const disk = await readFile(path);
    const markup = renderToStaticMarkup(<RawEvent line={disk.toString("utf8")} />);
    const panel = (mode: string) => htmlText(markup.match(new RegExp(`<pre data-raw-event="${mode}"[^>]*>([\\s\\S]*?)</pre>`))![1]!);
    assert.match(markup, /aria-pressed="true"[^>]*>formatted<\/button>/);
    assert.deepEqual(Buffer.from(panel("disk line")), disk);
    assert.deepEqual(jsonTokens(panel("formatted")), jsonTokens(line));
    assert.ok(panel("formatted").startsWith('{\n  "event": {'));
    assert.match(markup, /class="tok-num">1e0<\/span>/);
    const record = parseEventLine(line);
    const view = renderToStaticMarkup(<EventStream events={[record]} state="completed" />);
    assert.match(view, /2026-09-16T12:34:56.123456Z · \+00:00:00.000000 from run start/);
    assert.ok(!view.includes('title="1999'));
    assert.match(renderRun(completed, "tab=stream", { readError: "Unreadable record: missing envelope" }), /role="alert"/);
    console.log("raw formatting preserves tokens; disk-line toggle preserves bytes; capture time ignores payload ts");
  } finally { await rm(root, { recursive: true }); }
}
rawLineRoundTrip().catch((error) => { console.error(error); process.exitCode = 1; });

// Dedicated shared rows use stream context even when facets hide that context.
const sharedRows = [
  { ...runStart, condition: "0123456789abcdef", fetch_ref: "github:owner/repo/abc123?dir=packaging",
    params: { enabled: true, count: 2, nested: { values: [false, 1.25] } },
    runtime: { ...runStart.runtime,
      experiment_bin: "/nix/store/0123456789abcdefghijklmnpqrsvwxyz-govsim-adapter/bin/govsim",
      runner_bin: "/nix/store/0123456789abcdefghijklmnpqrsvwxyz-adb-runner/bin/adb-runner" },
    result_definitions: [{ name: "yield", label: "Harvest", unit: "fish", type: { kind: "float" } }, ...runDefinitions] },
  { type: "status", detail: "Old progress" },
  { type: "log", level: "warn", message: "First line\nSecond line" },
  { type: "stdout", line: "\x1b[31mterminal output\x1b[0m" },
  { type: "stderr", line: "\x1b]8;;https://example.org\x07linked error\x1b]8;;\x07" },
  { type: "result", name: "yield", value: 1.23456 },
  { type: "result", name: "yield", value: 2 },
  { type: "result", name: "collapsed", value: false },
  { type: "llm.call", output: { choices: [{ message: { content: "A reply" } }], usage: { input_tokens: 17, output_tokens: 5 } } },
  { type: "custom", kind: "test.message", data: { speaker: "Mayor", text: "Custom **hint**" } },
  { type: "status", detail: "Latest progress" },
  // Obsolete aggregate fields belong only in the raw record disclosure.
  { type: "run.end", state: "interrupted", duration_s: 65, exit_code: -15,
    usage_totals: { llm_calls: 999999, input_tokens: 999999 }, summary: { ignored: 999999 } },
].map((event, seq) => envelope(event, seq));
const diskRows = new Map(sharedRows.map((record) => [record.seq, { record, line: JSON.stringify(record) + "\n" }]));
const sharedView = (record: Ev) => renderToStaticMarkup(<EventStream review events={sharedRows}
  visibleEvents={[record]} definitions={hintDefinitions} state="interrupted" cid="condition" rid="run"
  diskRecords={diskRows} paramDeclarations={{ count: { type: { kind: "int" } } }} />);
const beforeRaw = (html: string) => html.slice(0, html.indexOf('<details data-raw-disclosure=""'));
assert.deepEqual([...new Set(sharedRows.map((r) => r.event.type))].sort(),
  ["custom", "llm.call", "log", "result", "run.end", "run.start", "status", "stderr", "stdout"]);
for (const record of sharedRows) {
  const view = sharedView(record);
  assert.match(view, new RegExp(`data-event-renderer="${record.event.type === "custom" ? "hint" : record.event.type}"`));
  assert.ok(!view.includes('data-event-renderer="raw"'));
  assert.match(view, /data-raw-disclosure/);
  if (record.event.type === "run.start" || record.event.type === "run.end") {
    // SSR includes the expanded contents even while <details> is closed. This
    // also checks the opened snapshot: toggling only changes the open attribute.
    for (const state of [view, view.replace(`<details id="ev-${record.seq}"`, `<details open="" id="ev-${record.seq}"`)]) {
      assert.ok(!beforeRaw(state).includes("<pre"));
      assert.match(state.slice(state.indexOf('data-raw-disclosure')), /<pre data-raw-event="formatted"/);
    }
  }
}
const startCard = beforeRaw(sharedView(sharedRows[0]!));
for (const field of ["Identity", "Provenance", "Runtime", "Inputs", "3 declared results", "govsim-adapter", "adb-runner", "3.13", "https://api.example.invalid"])
  assert.ok(startCard.includes(field), field);
for (const label of ["condition", "source", "tree hash", "experiment_bin", "runner_bin"])
  assert.match(startCard, new RegExp(`aria-label="Copy ${label}"`));
assert.match(startCard, /href="https:\/\/github.com\/owner\/repo\/tree\/abc123\/packaging"/);
assert.match(startCard, /href="#\/run\/condition\/run\?tab=summary"/);
assert.ok(!htmlText(startCard).includes("/nix/store/"));
const endFacts = beforeRaw(sharedView(sharedRows.at(-1)!));
for (const text of ["interrupted", "1m 5s", "exit -15 (SIGTERM)"])
  assert.ok(htmlText(endFacts).includes(text), text);
for (const text of ["derived:", "Derived from stream", "model calls", "input tokens", "output tokens", "results reported"])
  assert.ok(!htmlText(endFacts).includes(text), text);
assert.ok(!endFacts.includes("data-derived"));
assert.ok(!endFacts.includes("999999"));
const rowHeader = (view: string) => view.match(/<summary\b[\s\S]*?<\/summary>/)![0];
const endHeader = rowHeader(endFacts);
const endBody = endFacts.slice(endFacts.indexOf('data-run-end-facts=""'));
for (const facts of [endHeader, endBody]) {
  for (const text of ["interrupted", "1m 5s", "exit -15 (SIGTERM)"])
    assert.ok(htmlText(facts).includes(text), text);
}
// Removing the calls and repeated results cannot change the run.end presentation.
const lifecycleOnly = sharedRows.filter(({ event }) => event.type === "run.start" || event.type === "run.end");
const lifecycleEnd = beforeRaw(renderToStaticMarkup(<EventStream review events={lifecycleOnly}
  visibleEvents={[sharedRows.at(-1)!]} definitions={hintDefinitions} state="interrupted"
  cid="condition" rid="run" diskRecords={diskRows} />));
assert.equal(htmlText(rowHeader(lifecycleEnd)), htmlText(endHeader));
assert.equal(lifecycleEnd.slice(lifecycleEnd.indexOf('data-run-end-facts=""')), endBody);
assert.match(beforeRaw(sharedView(sharedRows[2]!)), /First line\nSecond line/);
assert.match(beforeRaw(sharedView(sharedRows[2]!)), /text-amber-700/);
for (const index of [3, 4]) {
  const line = beforeRaw(sharedView(sharedRows[index]!));
  assert.ok(!line.includes("\x1b"));
  assert.match(line, /font-mono/);
}
assert.match(beforeRaw(sharedView(sharedRows[5]!)), /title="yield"/);
assert.match(htmlText(beforeRaw(sharedView(sharedRows[5]!))), /Harvest1\.235fish/);
assert.match(htmlText(beforeRaw(sharedView(sharedRows[7]!))), /Collapsedno/);
assert.ok(!beforeRaw(sharedView(sharedRows[1]!)).includes("data-latest-status"));
assert.match(beforeRaw(sharedView(sharedRows[10]!)), /data-latest-status/);
console.log("shared row guard ok — lifecycle event facts, typed results, latest status, log levels, clean terminal lines");

// Producer toolchains use the shared schema hint without a bespoke renderer.
const producer = envelope({ type: "producer.python", implementation: "CPython", version: "3.13.14",
  platform: "Linux", flags: [] });
const producerView = renderToStaticMarkup(<EventStream review events={[producer]}
  definitions={hintDefinitions} state="completed" />);
assert.match(producerView, /data-event-renderer="hint"/);
assert.ok(htmlText(producerView).includes("CPython 3.13.14"));
assert.ok(!producerView.includes('data-event-renderer="raw"'));

// Native rows use the actual exported templates and never the unhinted fallback.
const govsimRows = events.filter((e) => e.event.kind?.startsWith("govsim."));
assert.ok(!govsimDefinitions.some((d) => d.kind === "govsim.pool"));
for (const record of govsimRows) {
  const view = beforeRaw(renderToStaticMarkup(<EventStream review events={events} visibleEvents={[record]}
    definitions={govsimDefinitions} state="completed" />));
  const hint = renderHint(record.event, govsimDefinitions)!;
  assert.ok(hint, record.event.kind);
  assert.match(view, /data-event-renderer="hint"/);
  assert.ok(!view.includes('data-event-renderer="raw"'));
  const text = htmlText(view);
  const title = hintText(hintValue(record.event, hint.title));
  if (title) assert.ok(text.includes(title), `${record.event.kind}: ${title}`);
  if (hint.format === "html-text") {
    assert.ok(text.includes(hintBody(record.event, hint)));
    assert.ok(!view.includes("<img") && !view.includes("<script"));
    assert.ok(!text.includes("<div") && !text.includes("</span>"));
  }
  if (record.event.kind === "govsim.state") assert.equal(view.includes("data-hint-badge"), record.event.data.final);
  if (record.event.kind === "govsim.memory") {
    for (const path of hint.fields!) assert.ok(view.includes(`title="${path}"`), path);
    assert.ok(view.indexOf('title="data.node.created"') < view.indexOf('title="data.node.expiration"'));
  }
}
// Missing optional fields vanish; zero, false, and explicit null remain evidence.
const fieldDefinitions = compileSchemas([{ oneOf: [{ properties: { type: { const: "custom" }, kind: { const: "test.fields" } },
  "x-adb-render": { icon: "info", title: "value {data.value} {data.missing}", badge: "data.final", format: "html-text",
    body: "data.html", fields: ["data.missing", "data.value", "data.final", "data.nullable"] } }] }]);
const fieldRow = envelope({ type: "custom", kind: "test.fields", data: {
  value: 0, final: true, nullable: null, html: '<p>Safe &amp; plain</p><script>alert(1)</script><img src=x onerror="alert(2)">',
} });
const fieldHtml = beforeRaw(renderToStaticMarkup(<EventStream events={[fieldRow]} definitions={fieldDefinitions} state="completed" />));
assert.match(htmlText(fieldHtml), /value 0/);
assert.match(fieldHtml, /data-hint-badge/);
assert.match(fieldHtml, /Safe &amp; plain/);
assert.ok(!fieldHtml.includes("alert(") && !fieldHtml.includes("<img") && !fieldHtml.includes('title="data.missing"'));
for (const field of ["value", "final", "nullable"]) assert.match(fieldHtml, new RegExp(`title="data.${field}"`));
console.log(`GovSim hint guard ok — ${govsimRows.length} native rows, templates, fields, final badges and inert HTML text`);

// Hidden earlier calls still supply history, and only an exact strict prefix is elided.
const input = [
  { role: "system", content: "First rule\nSecond rule" },
  { role: "user", content: "Old **question**" },
];
const continued = [...input,
  { role: "assistant", content: [{ type: "reasoning", reasoning: "Think briefly." }, { type: "text", text: "Using **lookup**." }],
    tool_calls: [{ id: "call-1", function: "lookup", arguments: { query: "fish" } }] },
  { role: "tool", tool_call_id: "call-1", content: "**Found** fish." },
  { role: "user", content: "Next **question**" },
];
const llmRows = [
  envelope({ type: "llm.call", agent: "a", input }, 0),
  envelope({ type: "llm.call", agent: "b", input: [] }, 1),
  envelope({ type: "llm.call", agent: "a", input: continued, model: "requested", via: "sdk", output: {
    model: "served", usage: { input_tokens_cache_read: 12 }, choices: [{ stop_reason: "max_tokens", message: { role: "assistant", content: "New **answer**" } }],
  } }, 2),
  envelope({ type: "llm.call", agent: "a", input }, 3),
];
const llmView = (seq: number) => beforeRaw(renderToStaticMarkup(<EventStream review events={llmRows}
  visibleEvents={[llmRows[seq]!]} definitions={hintDefinitions} state="completed" />));
const deltaView = llmView(2);
assert.match(deltaView, /show all 5 messages/);
assert.ok(!deltaView.includes("Old <b>question</b>") && !deltaView.includes("First rule"));
assert.match(deltaView, /Next <b>question<\/b>/);
assert.match(deltaView, /Using <b>lookup<\/b>/);
assert.match(deltaView, /New <b>answer<\/b>/);
assert.match(deltaView, /<details data-reasoning="" class=/); // No open attribute.
assert.match(htmlText(deltaView), /reasoning · 14 characters/);
assert.match(deltaView, /data-tool-call="call-1"[\s\S]*data-tool-arguments[\s\S]*data-tool-result="call-1"[\s\S]*<b>Found<\/b>/);
const callMeta = htmlText(deltaView.match(/<div[^>]*data-llm-meta=""[^>]*>([\s\S]*?)<\/div>/)![1]!);
for (const text of ["requested requested", "served served", "via sdk", "stop reason: max_tokens", "cache read: 12 tokens"])
  assert.ok(callMeta.includes(text), text);
const resetView = llmView(3);
assert.ok(!resetView.includes("history differs from the previous call"));
assert.ok(!resetView.includes("show all") && !resetView.includes("new messages"));
assert.match(resetView, /<details data-message-role="system" class=/);
assert.match(resetView, /<b>system<\/b> · First rule<\/summary>/);
assert.match(resetView, /Old <b>question<\/b>/);
assert.ok(!llmView(0).includes("history differs"));
console.log("model message guard ok — strict per-agent history, role formatting, tool result links and call metadata");

async function unreadableCorpusGuard() {
  const root = await copyBadRunCorpus();
  try {
    const reader = new RunReader(root, () => {});
    const rows = await reader.list();
    const badgeCount = (html: string) => html.match(/data-unreadable-badge=""/g)?.length ?? 0;
    const overview = renderToStaticMarkup(<OverviewView runs={rows} manifests={[{ name: "corpus", params: {} }]} />);
    const listing = renderToStaticMarkup(<RunsTable runs={rows} />);
    for (const view of [overview, listing]) {
      assert.equal(badgeCount(view), badCardNames.length);
      for (const name of badCardNames) {
        assert.match(view, new RegExp(`data-run="${name}"`));
        assert.ok(htmlText(view).includes(rows.find((r) => r.run === name)!.reason!));
      }
    }
    for (const name of badRunNames) assert.match(listing, new RegExp(`data-run="${name}"`));
    assert.match(listing, /data-run="20260916t120000z-000000000000"/);
    assert.match(listing, /Score/);
    assert.match(listing, /42/);
    assert.match(htmlText(overview), /2 completed/);
    for (const row of rows) {
      const snapshot = (await reader.read(row.condition, row.run, true))!;
      const rawRunJson = await reader.raw(row.condition, row.run);
      for (const tab of ["summary", "stream"]) {
        const page = renderToStaticMarkup(<RunView cid={row.condition} rid={row.run} query={`tab=${tab}`}
          events={snapshot.records?.map(({ record }) => record) ?? []} meta={row}
          readError={snapshot.meta.reason ?? null} rawRunJson={rawRunJson} />);
        assert.equal(badgeCount(page), snapshot.meta.readable ? 0 : 1);
        if (!snapshot.meta.readable) {
          assert.ok(htmlText(page).includes(snapshot.meta.reason!));
          assert.match(page, /data-raw-run-json/);
          assert.equal(htmlText(page.match(/<pre[^>]*>([\s\S]*?)<\/pre>/)![1]!), rawRunJson);
        } else assert.match(page, new RegExp(`data-run-tab="${tab}"`));
      }
    }
    const empty = renderToStaticMarkup(<RunView cid="corpus" rid="20260916t120000z-000000000005" events={[]}
      readError="no parseable run.json" rawRunJson={null} />);
    assert.equal(badgeCount(empty), 1);
    assert.match(empty, /No run.json recorded/);

    // Defense in depth: direct component callers cannot turn a foreign dict into
    // .map/.find/.some calls. Its values can still display as undeclared results.
    for (const foreign of [{ score: { type: { kind: "int" } } }, null, "score", [null]]) {
      const definitions = foreign as unknown as ResultDecl[];
      for (const view of [
        <ResultFacts definitions={definitions} summary={{ score: 42 }} />,
        <ResultRows definitions={definitions} summary={{ score: 42 }} />,
        <ResultChips definitions={definitions} summary={{ score: 42 }} />,
        <InstanceScoreChips definitions={definitions} scores={[{ score: 42 }]} />,
        <AggChips runs={[{ ...rows.find((r) => r.run === "20260916t120000z-000000000000")!, result_definitions: definitions }]} />,
      ]) assert.match(htmlText(renderToStaticMarkup(view)), /42/);
    }
    console.log("unreadable corpus guard ok — overview, runs list, both run tabs, exact raw metadata, all result guards; good run remains visible");
  } finally { await rm(root, { recursive: true }); }
}
unreadableCorpusGuard().catch((error) => { console.error(error); process.exitCode = 1; });
