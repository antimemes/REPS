/* App shell: left sidebar navigation + hash router (hand-rolled hook, no router
   dependency). Routes: #/ overview, #/experiments/<name>, #/runs, #/jobs
   (the launch queue + worker registry), #/runs/<rid>
   (the canonical run link — bare run id → resolver), and #/run/<cid>/<rid> (the
   resolved detail route). The shell is h-dvh; list pages scroll in <main>, the
   run page manages its own inner scrolling (fixed head/tabs, scrollable panes). */

import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { Sidebar } from "@/components/sidebar";
import { OverviewPage } from "@/pages/overview";
import { ExperimentPage } from "@/pages/experiment";
import { RunResolver, RunsPage } from "@/pages/runs";
import { RunPage } from "@/pages/run";
import { JobsPage } from "@/pages/jobs";
import { ConditionPage } from "@/pages/condition";
import { publishedMode } from "@/lib/data-source";
import { hashRoute } from "@/lib/run-view";

function useHash(): string {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener("hashchange", cb);
      return () => window.removeEventListener("hashchange", cb);
    },
    () => location.hash || "#/",
  );
}

function useTheme() {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try { localStorage.setItem("adb-theme", dark ? "dark" : "light"); } catch { /* private mode */ }
  }, [dark]);
  return { dark, toggle: () => setDark((d) => !d) };
}

export function App() {
  const hash = useHash();
  const { dark, toggle } = useTheme();
  const { parts, query } = hashRoute(hash);

  let section = "experiments";
  let page: ReactNode;
  let fullHeight = false;
  if (parts[0] === "run" && parts.length === 3) {
    section = "runs";
    fullHeight = true;
    page = <RunPage key={`${parts[1]}/${parts[2]}`} cid={parts[1]!} rid={parts[2]!} query={query} />;
  } else if (parts[0] === "conditions" && parts.length === 2) {
    section = "runs";
    page = <ConditionPage cid={decodeURIComponent(parts[1]!)} query={query} />;
  } else if (parts[0] === "experiments" && parts.length === 2) {
    page = <ExperimentPage key={parts[1]} name={decodeURIComponent(parts[1]!)} />;
  } else if (parts[0] === "runs" && parts.length === 2) {
    /* bare run id (lineage links) — resolve to the full #/run/<cid>/<rid> route */
    section = "runs";
    page = <RunResolver key={parts[1]} rid={parts[1]!} query={query} />;
  } else if (parts[0] === "runs") {
    section = "runs";
    page = <RunsPage />;
  } else if (parts[0] === "jobs" && !publishedMode()) {
    section = "jobs";
    page = <JobsPage />;
  } else {
    page = <OverviewPage />;
  }

  return (
    <div className="flex h-dvh flex-col overflow-hidden md:flex-row">
      <Sidebar section={section} dark={dark} onToggleTheme={toggle} />
      <main
        className={
          fullHeight
            ? "min-h-0 min-w-0 flex-1 overflow-hidden px-4 py-3 md:px-5 md:py-4"
            : "min-h-0 min-w-0 flex-1 overflow-y-auto px-4 py-3 pb-16 md:px-5 md:py-4 md:pb-16"
        }
      >
        <div className={fullHeight ? "mx-auto h-full max-w-6xl" : "mx-auto max-w-6xl"}>
          {page}
        </div>
      </main>
    </div>
  );
}
