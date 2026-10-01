/* Persistent left navigation: brand, section links with active state, and at the
   bottom a deliberately subtle webui↔server CONNECTIVITY indicator (run liveness
   lives next to the streams it vouches for — components/bits.tsx LiveDot), the
   command-settings menu, and the theme toggle. Sections map onto the hash routes;
   the run page highlights Runs, experiment pages highlight Experiments. */

import { FlaskConical, List, Moon, Server, Sun } from "lucide-react";
import { JOB_TERMINAL, useJobsPoll, usePollHealth, useExecutorPoll } from "@/lib/data";
import { Button } from "@/components/ui/button";
import { CmdSettings } from "@/components/cmd-settings";
import { cn } from "@/lib/utils";

import { publishedMode } from "@/lib/data-source";

const NAV = [
  { section: "experiments", href: "#/", label: "Experiments", Icon: FlaskConical },
  { section: "runs", href: "#/runs", label: "Runs", Icon: List },
  { section: "jobs", href: "#/jobs", label: "Jobs", Icon: Server },
] as const;

export function Sidebar({
  section,
  dark,
  onToggleTheme,
}: {
  section: string;
  dark: boolean;
  onToggleTheme: () => void;
}) {
  /* queued+running job count on the Jobs entry — the queue is the one thing
     that changes while you're elsewhere. Gated callers (null) just get no badge. */
  const jobs = useJobsPoll();
  const executor = useExecutorPoll();
  const active = jobs?.filter((j) => !JOB_TERMINAL.has(j.state)).length ?? 0;
  return (
    <aside className="flex w-full shrink-0 items-center border-b bg-card/50 px-2 md:w-44 md:flex-col md:items-stretch md:border-r md:border-b-0 md:px-0">
      <a href="#/" className="flex min-h-10 items-center font-mono text-lg font-bold tracking-widest no-underline md:px-4 md:py-3.5">
        adb
      </a>
      <nav className="flex gap-2 md:block md:flex-1 md:space-y-0.5 md:px-2">
        {NAV.filter((item) => item.section !== "jobs" || executor?.enabled).map(({ section: s, href, label, Icon }) => (
          <a
            key={s}
            href={href}
            className={cn(
              "flex min-h-10 items-center gap-1 rounded-md px-1.5 py-1.5 text-xs text-muted-foreground no-underline transition-colors hover:bg-accent hover:text-accent-foreground md:min-h-0 md:gap-2.5 md:px-2.5 md:text-sm",
              s === section && "bg-accent font-medium text-accent-foreground",
            )}
          >
            <Icon className="size-4 shrink-0" />
            {label}
            {s === "jobs" && active > 0 && (
              <span className="ml-auto rounded-full bg-primary/15 px-1.5 text-[10px] font-medium tabular-nums text-primary">
                {active}
              </span>
            )}
          </a>
        ))}
      </nav>
      <div className="relative ml-auto flex items-center md:static md:ml-0 md:block md:space-y-1 md:border-t md:p-2">
        {!publishedMode() && <ConnectedDot />}
        <CmdSettings />
        <Button
          variant="ghost"
          size="sm"
          className="h-10 w-7 gap-0 px-0 text-muted-foreground max-md:has-[>svg]:px-0 md:h-8 md:w-full md:justify-start md:gap-2.5 md:px-2.5"
          onClick={onToggleTheme}
          aria-label="toggle theme"
        >
          {dark ? <Sun /> : <Moon />}
          <span className="hidden md:inline">{dark ? "light mode" : "dark mode"}</span>
        </Button>
      </div>
    </aside>
  );
}

function ConnectedDot() {
  const { live } = usePollHealth();
  return (
    <div
      className="flex items-center gap-2 px-1 py-0.5 text-[11px] text-muted-foreground/80 md:px-2.5"
      title="webui ↔ adb-web server connectivity (background polls, every 2s). Not run liveness — that dot sits next to each live stream."
    >
      <span className={cn("size-1.5 rounded-full", live ? "bg-emerald-500" : "bg-amber-500")} />
      <span className="hidden md:inline">{live ? "connected" : "offline"}</span>
    </div>
  );
}
