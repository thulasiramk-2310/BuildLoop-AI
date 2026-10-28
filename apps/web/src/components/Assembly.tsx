import clsx from "clsx";
import { api } from "../api";
import type { ProjectView } from "../api";
import { useUi } from "../store";
import { Badge, Button, Empty, useAction } from "./ui";

export default function Assembly({ view }: { view: ProjectView | undefined }) {
  const pid = useUi((s) => s.projectId);
  const setTab = useUi((s) => s.setTab);
  const { run, busy } = useAction();
  if (!view || !pid) return <Empty title="No project selected" />;
  const did = view.state.active_design_id;
  const steps = did ? view.state.steps_by_design[did] : undefined;
  if (!did || !steps) return <Empty title="No approved design" hint="Approve a design in the Design tab to get step-by-step assembly guidance." />;
  const design = view.designs.find((d) => d.id === did)!;
  const names = new Map(design.components.map((c) => [c.id, c.name]));
  const current = steps.find((s) => !s.confirmed);
  const label = (ref: string) => {
    const [c, p] = ref.split(".");
    return `${names.get(c) ?? c} ▸ ${p}`;
  };

  return (
    <div className="p-3 text-xs">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold">{design.title}</h3>
        <Badge tone="planned">proposed overlay, not observed</Badge>
      </div>
      <ol className="space-y-2">
        {steps.map((s) => {
          const isCur = s === current;
          return (
            <li key={s.index} className={clsx("rounded border p-3", s.confirmed ? "border-ok/30 bg-ok/5" : isCur ? "border-accent bg-panel-2" : "border-line opacity-60")}>
              <div className="flex items-center gap-2">
                <span className="font-mono text-faint">{String(s.index).padStart(2, "0")}</span>
                <b className="flex-1">{s.title}</b>
                {s.confirmed && <Badge tone="user_confirmed">confirmed by you</Badge>}
              </div>
              {(isCur || s.confirmed) && (
                <>
                  <p className="mt-1 text-dim">{s.detail}</p>
                  {s.connection_ids.length > 0 && (
                    <ul className="mt-2 space-y-0.5 font-mono text-[11px]">
                      {s.connection_ids.map((id) => {
                        const c = design.connections.find((x) => x.id === id);
                        return c && <li key={id} className="text-info">{label(c.src)} → {label(c.dst)}</li>;
                      })}
                    </ul>
                  )}
                </>
              )}
              {isCur && (
                <Button className="mt-3" variant="primary" busy={busy === "step"} onClick={() => run("step", () => api.confirmStep(pid, did, s.index))}>
                  I completed and checked this step
                </Button>
              )}
            </li>
          );
        })}
      </ol>
      {!current && (
        <div className="mt-3 rounded border border-ok/40 bg-ok/10 p-3 text-ok">
          All steps confirmed. BuildLoop cannot see your wiring; your confirmation is the record.
          <div className="mt-2">
            <Button variant="primary" onClick={() => setTab("hardware")}>Continue to hardware</Button>
          </div>
        </div>
      )}
    </div>
  );
}
