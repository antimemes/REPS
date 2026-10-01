/* Runs (#/runs) — the flat run table across all experiments, plus the shared
   RunsTable component the experiment pages reuse. Param filters chosen on
   experiment pages stay scoped to those pages — this tab always shows everything. */

import { useEffect } from "react";
import type { RunMeta } from "@/shared/types";
import {
  displayState, fmtVal, groupBy, paramsOf, prefetchRun,
  useRunsPoll, variedKeys,
} from "@/lib/data";
import { navigateWithGlow } from "@/lib/nav";
import { Card } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { PageLoading, StateBadge } from "@/components/bits";
import { ParamChip } from "@/components/param-value";
import { RenderBoundary, UnreadableBadge, displayRun } from "@/components/read-errors";
import { ResultChips } from "@/components/results";
import { conditionHref } from "@/lib/conditions";
import { cn } from "@/lib/utils";

export function RunsPage() {
  const runs = useRunsPoll();
  if (runs === null) return <PageLoading />;
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold">Runs</h2>
      <RunsTable runs={runs} />
    </div>
  );
}

/* #/runs/<rid> — resolve a bare run id (lineage links carry no condition) to the
   full run route once the run list knows it */
export function RunResolver({ rid, query = "" }: { rid: string; query?: string }) {
  const runs = useRunsPoll();
  const target = runs?.find((r) => r.run === rid);
  useEffect(() => {
    if (target) location.replace(`#/run/${target.condition}/${target.run}${query ? `?${query}` : ""}`);
  }, [target, query]);
  if (runs === null) return <PageLoading />;
  if (!target)
    return (
      <p className="text-sm text-muted-foreground">
        run <span className="font-mono">{rid}</span> is not in this local store —{" "}
        <a href="#/runs">all runs</a>
      </p>
    );
  return <PageLoading />;
}

export function RunsTable({ runs: suppliedRuns, hideExperiment = false }: { runs: RunMeta[]; hideExperiment?: boolean }) {
  const runs = (Array.isArray(suppliedRuns) ? suppliedRuns : []).map(displayRun);
  const varied: Record<string, string[]> = Object.create(null);
  for (const [exp, rs] of Object.entries(groupBy(runs, (r) => r.experiment))) {
    // Parameter comparison is optional context. A bad parameter value is exposed
    // by its own row boundary, without preventing neighboring rows from rendering.
    try { varied[exp] = variedKeys(rs.filter((r) => r.readable !== false)); }
    catch { varied[exp] = []; }
  }
  const cols = hideExperiment
    ? ["condition", "run", "params", "state", "results", "started"]
    : ["condition", "run", "experiment", "params", "state", "results", "started"];
  return (
    <Card className="overflow-hidden py-0">
      <Table className="max-md:block max-md:[&_td]:block max-md:[&_td]:min-w-0 max-md:[&_td]:border-0 max-md:[&_td]:p-0 max-md:[&_td]:[overflow-wrap:anywhere]">
        <TableHeader className="max-md:hidden">
          <TableRow>
            {cols.map((h) => <TableHead key={h}>{h}</TableHead>)}
          </TableRow>
        </TableHeader>
        <TableBody className="max-md:block">
          {runs.length === 0 && (
            <TableRow className="max-md:block max-md:p-3">
              <TableCell colSpan={cols.length} className="text-muted-foreground">no runs match</TableCell>
            </TableRow>
          )}
          {runs.map((r) => <RenderBoundary key={`${r.condition}/${r.run}`} resetKey={r}
            fallback={(reason) => <UnreadableRow run={r} reason={reason} columns={cols.length} />}>
            {r.readable === false ? <UnreadableRow run={r} reason={r.reason!} columns={cols.length} />
              : <RunRow run={r} varied={varied[r.experiment] ?? []} hideExperiment={hideExperiment} />}
          </RenderBoundary>)}
        </TableBody>
      </Table>
    </Card>
  );
}

function UnreadableRow({ run, reason, columns }: { run: RunMeta; reason: string; columns: number }) {
  return <TableRow data-run={run.run} className="max-md:block max-md:p-3">
    <TableCell colSpan={columns}>
      <a href={`#/run/${encodeURIComponent(run.condition)}/${encodeURIComponent(run.run)}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
        <UnreadableBadge /><code className="text-xs">{run.run}</code>
        <span className="text-xs text-muted-foreground">{reason}</span>
      </a>
    </TableCell>
  </TableRow>;
}

function RunRow({ run: r, varied, hideExperiment }: { run: RunMeta; varied: string[]; hideExperiment: boolean }) {
  const p = paramsOf(r);
  const requested = typeof p?.model === "string" ? p.model.replace(/^[^/]+\//, "") : undefined;
  const served = Array.isArray(r.derived?.served_models) ? r.derived.served_models : [];
  const different = served.filter((model) => model !== requested);
  const vk = [...new Set([...varied, ...(requested && different.length ? ["model"] : [])])];
  const previewParam = vk.includes("model") ? "model" : vk[0];
  const allParams = p
    ? Object.entries(p).map(([k, v]) => `${k}=${fmtVal(v)}`).join("\n")
    : "";
  return (
    <TableRow
      data-run={r.run}
      className="cursor-pointer max-md:grid max-md:grid-cols-[auto_minmax(0,1fr)] max-md:gap-x-2 max-md:gap-y-1 max-md:p-3"
      onClick={(e) =>
        /* canonical run link — the bare-id route the runner also prints;
           the resolver bounces to the full route instantly (list cached) */
        void navigateWithGlow(e.currentTarget, `#/runs/${r.run}`,
          () => prefetchRun(r.condition, r.run))}
    >
      <TableCell className="font-mono text-xs max-md:col-start-1 max-md:row-start-3"><a href={conditionHref(r.condition)} onClick={(e) => e.stopPropagation()} className="hover:underline">{(r.condition ?? "").slice(0, 12)}</a></TableCell>
      <TableCell className="font-mono text-xs max-md:col-span-2 max-md:row-start-2">{r.run}</TableCell>
      {!hideExperiment && <TableCell className="max-md:col-start-2 max-md:row-start-3 max-md:text-xs max-md:text-muted-foreground">{r.experiment}</TableCell>}
      <TableCell title={allParams} className="max-md:col-span-2 max-md:row-start-4">
        {!p ? (
          <span className="text-muted-foreground">…</span>
        ) : vk.length ? (
          <span className="flex flex-wrap gap-1">
            {vk.map((k) => <span key={k} className={cn("contents", k !== previewParam && "max-md:hidden")}>
              <ParamChip name={k} value={p[k]} />
            </span>)}
            {vk.length > 1 && <span className="inline-flex items-center rounded-full border bg-muted/50 px-2 text-[11px] md:hidden">
              +{vk.length - 1} params
            </span>}
            {different.length > 0 && <span className="text-xs text-muted-foreground max-md:hidden" title="Model names returned by the endpoint">
              → served: {different.join(", ")}
            </span>}
          </span>
        ) : (
          <span className="text-muted-foreground">{different.length ? `served: ${different.join(", ")}` : "—"}</span>
        )}
      </TableCell>
      <TableCell className="max-md:col-start-1 max-md:row-start-1"><StateBadge state={displayState(r)} /></TableCell>
      <TableCell className="max-md:col-span-2 max-md:row-start-5">
        <span className="flex flex-wrap gap-1">
          <ResultChips summary={r.summary} definitions={r.result_definitions} mobileLimit={3} />
        </span>
      </TableCell>
      <TableCell className="whitespace-nowrap text-xs text-muted-foreground max-md:col-start-2 max-md:row-start-1 max-md:justify-self-end">
        {(r.started_at ?? "").replace("T", " ").slice(0, 19)}
      </TableCell>
    </TableRow>
  );
}
