import clsx from "clsx";
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { ApiFail } from "../api";
import { useUi } from "../store";

export function Panel({ title, actions, children, className }: { title: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={clsx("flex min-h-0 flex-col bg-panel", className)} aria-label={title}>
      <header className="flex h-9 shrink-0 items-center justify-between border-b border-line px-3">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.08em] text-dim">{title}</h2>
        <div className="flex items-center gap-2">{actions}</div>
      </header>
      <div className="min-h-0 flex-1 overflow-auto">{children}</div>
    </section>
  );
}

type Variant = "primary" | "ghost" | "danger";
export function Button({ variant = "ghost", busy, className, children, ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; busy?: boolean }) {
  return (
    <button
      {...p}
      disabled={p.disabled || busy}
      className={clsx(
        "inline-flex h-7 items-center gap-1.5 rounded border px-2.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40",
        variant === "primary" && "border-accent bg-accent/15 text-accent hover:bg-accent/25",
        variant === "ghost" && "border-line text-ink hover:bg-panel-2",
        variant === "danger" && "border-bad/60 text-bad hover:bg-bad/15",
        className,
      )}
    >
      {busy && <span className="size-3 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />}
      {children}
    </button>
  );
}

const TONES: Record<string, string> = {
  PASS: "text-ok border-ok/40 bg-ok/10",
  WARNING: "text-warn border-warn/40 bg-warn/10",
  BLOCKED: "text-bad border-bad/40 bg-bad/10",
  UNKNOWN: "text-info border-info/40 bg-info/10",
  detected: "text-info border-info/40 bg-info/10",
  uncertain: "text-warn border-warn/40 bg-warn/10",
  user_confirmed: "text-ok border-ok/40 bg-ok/10",
  planned: "text-dim border-line bg-panel-2",
  assembled: "text-info border-info/40 bg-info/10",
  validated: "text-ok border-ok/40 bg-ok/10",
  physically_tested: "text-accent border-accent/40 bg-accent/10",
  failed: "text-bad border-bad/40 bg-bad/10",
  demo: "text-warn border-warn/50 bg-warn/10",
  real: "text-ok border-ok/40 bg-ok/10",
  simulated: "text-warn border-warn/50 bg-warn/10",
  neutral: "text-dim border-line bg-panel-2",
};

export function Badge({ tone, children, title }: { tone: string; children: ReactNode; title?: string }) {
  return (
    <span title={title} className={clsx("inline-flex items-center rounded border px-1.5 py-px font-mono text-[10px] uppercase tracking-wide", TONES[tone] ?? TONES.neutral)}>
      {children}
    </span>
  );
}

export function Empty({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex h-full min-h-24 flex-col items-center justify-center gap-1 p-6 text-center">
      <p className="text-sm text-dim">{title}</p>
      {hint && <p className="max-w-xs text-xs text-faint">{hint}</p>}
    </div>
  );
}

export function Kv({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5">
      <dt className="text-faint">{k}</dt>
      <dd className="text-right font-mono text-xs text-ink">{v}</dd>
    </div>
  );
}

/** Runs an async action, surfaces ApiFail as a toast, refreshes project data. */
export function useAction() {
  const qc = useQueryClient();
  const notify = useUi((s) => s.notify);
  const pid = useUi((s) => s.projectId);
  const [busy, setBusy] = useState<string | null>(null);
  const run = useCallback(
    async <T,>(key: string, fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
      setBusy(key);
      try {
        const out = await fn();
        if (ok) notify({ kind: "ok", text: ok });
        return out;
      } catch (e) {
        if (e instanceof ApiFail) notify({ kind: "error", text: e.message, hint: e.hint });
        else notify({ kind: "error", text: e instanceof Error ? e.message : String(e) });
        return undefined;
      } finally {
        setBusy(null);
        void qc.invalidateQueries({ queryKey: ["view", pid] });
      }
    },
    [qc, notify, pid],
  );
  return { run, busy };
}

export function Toast() {
  const t = useUi((s) => s.toast);
  const notify = useUi((s) => s.notify);
  // Errors stay until dismissed; confirmations fade so they never cover controls.
  useEffect(() => {
    if (!t || t.kind === "error") return;
    const id = setTimeout(() => notify(null), 3500);
    return () => clearTimeout(id);
  }, [t, notify]);
  if (!t) return null;
  const tone = t.kind === "error" ? "border-bad/60" : t.kind === "ok" ? "border-ok/60" : "border-info/60";
  return (
    <div role="status" aria-live="polite" className={clsx("fixed bottom-4 left-1/2 z-50 w-[min(520px,92vw)] -translate-x-1/2 rounded border bg-panel-2 p-3 shadow-xl", tone)}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs text-ink">{t.text}</p>
          {t.hint && <p className="mt-1 text-xs text-dim">{t.hint}</p>}
        </div>
        <button aria-label="Dismiss" className="text-dim hover:text-ink" onClick={() => notify(null)}>
          ×
        </button>
      </div>
    </div>
  );
}
