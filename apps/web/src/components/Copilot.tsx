import { useState } from "react";
import { api } from "../api";
import type { ProjectView } from "../api";
import { useUi } from "../store";
import MetricsPanel from "./MetricsPanel";
import { Badge, Button, Empty, useAction } from "./ui";

export default function Copilot({ view }: { view: ProjectView | undefined }) {
  const pid = useUi((s) => s.projectId);
  const setTab = useUi((s) => s.setTab);
  const setDesign = useUi((s) => s.setDesign);
  const { run, busy } = useAction();
  const [goal, setGoal] = useState("Build a light that turns on when the environment gets dark.");
  if (!view || !pid) return <Empty title="No project selected" hint="Create a Demo project to try the full loop with recorded data and simulated hardware." />;
  const p = view.project;
  const redesign = p.stage === "REDESIGN";
  const invalidated = Object.entries(view.state.invalidated);
  const noInventory = view.components.length === 0;

  const plan = async () => {
    const out = await run("plan", () => api.plan(pid, goal), "Designs proposed and validated");
    if (out?.length) {
      setDesign(out[0].id);
      setTab("design");
    }
  };

  return (
    <div className="flex flex-col gap-3 p-3 text-xs">
      <div>
        <label htmlFor="goal" className="mb-1 block text-[11px] font-semibold uppercase tracking-wider text-dim">
          Engineering goal
        </label>
        <textarea id="goal" value={goal} onChange={(e) => setGoal(e.target.value)} rows={2} className="w-full resize-none rounded border border-line bg-bg p-2 text-xs" />
        <div className="mt-2 flex items-center gap-2">
          <Button variant="primary" busy={busy === "plan"} disabled={noInventory} onClick={plan}>
            {redesign ? "Propose redesign" : "Design with Muse Spark"}
          </Button>
          <Badge tone={p.mode === "demo" ? "demo" : "real"}>{p.mode === "demo" ? "deterministic planner (demo fixture)" : "muse-spark-1.3"}</Badge>
        </div>
        {noInventory && <p className="mt-1 text-faint">Scan or add components first. The planner only uses what is in inventory.</p>}
      </div>

      {redesign && (
        <div role="alert" className="rounded border border-warn/50 bg-warn/10 p-3">
          <p className="mb-1 font-semibold text-warn">Reality changed. The active design no longer matches your hardware.</p>
          <ul className="list-disc pl-4 text-dim">
            {invalidated.flatMap(([id, rs]) => rs.map((r, i) => <li key={id + i}>{r}</li>))}
          </ul>
          {view.revisions.some((r) => r.kind === "inventory_change" && r.data?.manual_override) && (
            <p className="mt-2 text-warn">This change is a manual override: the last camera frame still shows the part. Rescan to have the camera confirm the removal.</p>
          )}
          <p className="mt-2 text-dim">Review the inventory change in the timeline, then propose and approve a redesign.</p>
        </div>
      )}

      <div>
        <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-dim">What happens next</h3>
        <ol className="space-y-1 text-dim">
          <li>1. Scan the bench, correct anything wrong, confirm resistor values.</li>
          <li>2. Design: proposals are checked by deterministic rules before you see them.</li>
          <li>3. Build step by step. You confirm each wiring step; nothing is assumed.</li>
          <li>4. Connect a board, authorize, test. Evidence is graded honestly.</li>
          <li>5. Remove a part. BuildLoop invalidates the design and proposes a feasible alternative.</li>
        </ol>
      </div>

      <MetricsPanel />

      <div>
        <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-dim">Decision trace</h3>
        {view.decision_trace.length === 0 ? (
          <p className="text-faint">No model decisions recorded yet. Traces list tool calls and results, never hidden reasoning.</p>
        ) : (
          <ul className="space-y-1.5 font-mono text-[11px]">
            {view.decision_trace.map((t, i) => (
              <li key={i} className="rounded border border-line bg-bg p-2 text-dim">
                {JSON.stringify(t).slice(0, 420)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
