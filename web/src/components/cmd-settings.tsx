/* Bottom-left settings menu: customizes how `nix run` commands render across
   the webui (today: the run-config builder's oneliner). Mirrors the docs gear
   menu — same options, same localStorage key (lib/cmd-prefs.ts), so the choice
   follows the user between guide and webui. */

import { useEffect, useRef, useState } from "react";
import { Settings } from "lucide-react";
import { Button } from "@/components/ui/button";
import { setCmdPrefs, useCmdPrefs } from "@/lib/cmd-prefs";
import { previewCmd, type CmdPrefs } from "@/lib/cmd-rewrite";
import { cn } from "@/lib/utils";

/* like bits.tsx Segmented, but options carry a label distinct from the stored
   value ("local checkout (.)" vs "local") */
function Seg<K extends string>({ value, options, onChange }: {
  value: K;
  options: readonly { value: K; label: string }[];
  onChange: (v: K) => void;
}) {
  return (
    <span className="inline-flex overflow-hidden rounded-md border text-[11px]">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={cn(
            "px-2 py-0.5",
            o.value === value
              ? "bg-accent font-medium text-accent-foreground"
              : "text-muted-foreground hover:bg-muted/60",
          )}
        >
          {o.label}
        </button>
      ))}
    </span>
  );
}

function Check({ label, checked, onChange }: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-1.5 text-[11px] text-muted-foreground">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      {label}
    </label>
  );
}

export function CmdSettings() {
  const [open, setOpen] = useState(false);
  const p = useCmdPrefs();
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onDown = (e: PointerEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [open]);

  const set = (patch: Partial<CmdPrefs>) => setCmdPrefs(patch);

  return (
    <div ref={wrap} className="md:relative">
      <Button
        variant="ghost"
        size="sm"
        className="h-10 w-7 gap-0 px-0 text-muted-foreground max-md:has-[>svg]:px-0 md:h-8 md:w-full md:justify-start md:gap-2.5 md:px-2.5"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="true"
        aria-expanded={open}
        aria-label="settings"
      >
        <Settings />
        <span className="hidden md:inline">settings</span>
      </Button>

      {open && (
        <div
          role="menu"
          className="absolute top-full right-0 z-50 mt-2 w-[min(24rem,calc(100vw-2rem))] space-y-2 rounded-lg border bg-background p-3 shadow-lg md:top-auto md:right-auto md:bottom-full md:left-0 md:mt-0 md:mb-2"
        >
          <div className="text-xs font-semibold">Commands adapt to your setup</div>
          <code className="block overflow-x-auto rounded border bg-muted/40 p-2 font-mono text-[11px] whitespace-pre">
            {previewCmd(p)}
          </code>

          <div className="flex items-center gap-2">
            <span className="w-9 shrink-0 text-[11px] text-muted-foreground">From</span>
            <Seg
              value={p.source}
              options={[
                { value: "github", label: "GitHub" },
                { value: "local", label: "local checkout (.)" },
              ]}
              onChange={(v) => set({ source: v })}
            />
          </div>

          {p.source === "github" && (
            /* a cached main.tar.gz can lag behind new commits; this adds
               --tarball-ttl 0 / --refresh so every run re-checks. Inert for a
               local checkout, so the row hides with it. */
            <div className="pl-11">
              <Check label="always fetch latest" checked={p.latest}
                onChange={(v) => set({ latest: v })} />
            </div>
          )}

          {/* the mode tabs — the rows below are contextual to the tab */}
          <div className="flex items-center gap-2">
            <span className="w-9 shrink-0 text-[11px] text-muted-foreground">With</span>
            <Seg
              value={p.mode}
              options={[
                { value: "nix-build", label: "nix-build" },
                { value: "flakes", label: "flakes" },
                { value: "nix-run", label: "nix-run" },
              ]}
              onChange={(v) => set({ mode: v })}
            />
          </div>

          {p.mode === "flakes" && (
            /* the registry toggle is meaningless for a local checkout; the
               global-flakes toggle decides whether commands carry the armor flag */
            <div className="flex flex-wrap gap-x-4 gap-y-1 pl-11">
              <Check label="flakes enabled globally" checked={p.flakes}
                onChange={(v) => set({ flakes: v })} />
              {p.source === "github" && (
                <Check label="adb registry added" checked={p.registry}
                  onChange={(v) => set({ registry: v })} />
              )}
            </div>
          )}

          {p.mode === "nix-run" && (
            <div className="pl-11">
              <Check label="nix-run installed globally" checked={p.nixRun}
                onChange={(v) => set({ nixRun: v })} />
            </div>
          )}

          <p className="text-[10px] leading-snug text-muted-foreground">
            Applies to the run-config builder, and is shared with the user guide's
            command settings (same browser storage). See “Working with Nix” in the
            guide for what each toggle means.
          </p>
        </div>
      )}
    </div>
  );
}
