import clsx from "clsx";
import { api } from "../api";
import type { ProjectView, ViewDesign } from "../api";
import { useUi } from "../store";
import type { DesignCandidate, ValidationResult } from "../types";
import { Badge, Button, Empty, Kv, useAction } from "./ui";

function Results({ items }: { items: ValidationResult[] }) {
  return (
    <ul className="space-y-1.5">
      {items.map((v, i) => (
        <li key={i} className="rounded border border-line bg-bg p-2">
          <div className="flex items-center gap-2">
            <Badge tone={v.status}>{v.status}</Badge>
            <span className="font-mono text-[11px] text-dim">{v.rule}</span>
            {v.estimated && <Badge tone="neutral" title="Computed from assumptions, not measured">estimate</Badge>}
          </div>
          <p className="mt-1 text-xs text-ink">{v.message}</p>
          {v.fix && <p className="mt-0.5 text-xs text-warn">Fix: {v.fix}</p>}
        </li>
      ))}
    </ul>
  );
}

function Comparison({ view }: { view: ProjectView }) {
  const c = view.comparison;
  if (!c.original) return null;
  const col = (d: ViewDesign | null, label: string) =>
    d && (
      <div className="min-w-0 flex-1 rounded border border-line bg-bg p-2">
        <p className="text-[10px] uppercase tracking-wider text-faint">{label}</p>
        <p className="mb-1 text-xs font-semibold">{d.title}</p>
        <Kv k="Sensor" v={d.sensor.replace(/_/g, " ")} />
        <Kv k="Validation" v={<Badge tone={d.overall}>{d.overall}</Badge>} />
        <Kv k="Evidence level" v={d.verification ? d.verification.level.replace(/_/g, " ") : "not tested"} />
        {d.verification && <p className={clsx("mt-1 text-[11px]", d.verification.passed === false ? "text-bad" : "text-dim")}>{d.verification.summary}</p>}
        {d.invalidated_reasons.length > 0 && <p className="mt-1 text-[11px] text-bad">Invalidated: {d.invalidated_reasons.join("; ")}</p>}
        {d.substitute_notes.map((n) => (
          <p key={n} className="mt-1 text-[11px] text-warn">{n}</p>
        ))}
      </div>
    );
  return (
    <div className="border-t border-line p-3">
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-dim">Before / after</h3>
      {c.removed_components.length > 0 && <p className="mb-2 text-xs text-warn">Removed: {c.removed_components.join(", ")}</p>}
      <div className="flex gap-2">{col(c.original, "Original")}{col(c.revised, "Revised")}</div>
      <h4 className="mb-1 mt-3 text-[10px] uppercase tracking-wider text-faint">Unresolved limitations</h4>
      <ul className="list-disc pl-4 text-[11px] text-dim">{c.limitations.map((l) => <li key={l}>{l}</li>)}</ul>
    </div>
  );
}

export default function Inspector({ view }: { view: ProjectView | undefined }) {
  const pid = useUi((s) => s.projectId);
  const designId = useUi((s) => s.designId);
  const setDesign = useUi((s) => s.setDesign);
  const setTab = useUi((s) => s.setTab);
  const { run, busy } = useAction();
  if (!view || !pid) return <Empty title="No project selected" />;
  if (view.designs.length === 0) return <Empty title="No designs yet" hint="Ask the Engineering Copilot to design from your inventory." />;

  const d: DesignCandidate = view.designs.find((x) => x.id === designId) ?? view.designs.at(-1)!;
  const active = view.state.active_design_id === d.id;
  const dead = view.state.invalidated[d.id];
  // Re-planning adds new candidates; show the latest per title (plus the approved one) so the list stays readable.
  const latest = new Map(view.designs.map((x) => [x.title, x]));
  const approved = view.designs.find((x) => x.id === view.state.active_design_id);
  if (approved) latest.set(approved.title, approved);
  const shown = [...latest.values()];
  const bhv = d.behavior;
  const nameOf = new Map(d.components.map((c) => [c.id, c.name]));
  const pretty = (ref: string) => {
    const [id, pin] = ref.split(".");
    return `${nameOf.get(id) ?? id}.${pin}`;
  };
  const approve = async () => {
    const steps = await run("approve", () => api.approve(pid, d.id), "Design approved. Follow the assembly steps.");
    if (steps) setTab("assembly");
  };

  return (
    <div className="text-xs">
      <div className="flex flex-wrap gap-1 border-b border-line p-2">
        {shown.map((x) => (
          <button key={x.id} onClick={() => setDesign(x.id)} aria-pressed={x.id === d.id} className={clsx("flex items-center gap-1.5 rounded border px-2 py-1", x.id === d.id ? "border-accent text-accent" : "border-line text-dim hover:text-ink")}>
            {x.title}
            <Badge tone={view.state.invalidated[x.id] ? "BLOCKED" : x.validation.length ? (x.validation.some((v) => v.status === "BLOCKED") ? "BLOCKED" : x.validation.some((v) => v.status === "UNKNOWN") ? "UNKNOWN" : x.validation.some((v) => v.status === "WARNING") ? "WARNING" : "PASS") : "neutral"}>
              {view.state.invalidated[x.id] ? "INVALID" : x.validation.some((v) => v.status === "BLOCKED") ? "BLOCKED" : x.validation.some((v) => v.status === "UNKNOWN") ? "UNKNOWN" : x.validation.some((v) => v.status === "WARNING") ? "WARNING" : "PASS"}
            </Badge>
          </button>
        ))}
      </div>
      <div className="space-y-3 p-3">
        <div>
          <h3 className="text-sm font-semibold">{d.title}</h3>
          <p className="text-dim">{d.goal}</p>
          <p className="mt-1 text-faint">Source: {d.source}{d.source === "demo-fixture" ? " (deterministic planner, not a model)" : d.source.startsWith("open-weight") ? " (Meta open-weight model via a partner; NOT the Meta Model API)" : d.source === "muse-spark" ? " (Meta Model API · Muse Spark)" : ""}</p>
        </div>
        {dead && (
          <div role="alert" className="rounded border border-bad/50 bg-bad/10 p-2 text-bad">
            Invalidated by inventory change: {dead.join("; ")}
          </div>
        )}
        {d.substitute_notes.length > 0 && (
          <div className="rounded border border-warn/40 bg-warn/10 p-2 text-warn">
            {d.substitute_notes.map((n) => <p key={n}>{n}</p>)}
          </div>
        )}
        <dl>
          <Kv k="Board" v={d.board} />
          <Kv k="Sensor" v={bhv.sensor_source.replace(/_/g, " ")} />
          <Kv k="LED pin" v={`GPIO${bhv.led_pin}`} />
          {bhv.sensor_pin != null && <Kv k="Sensor pin" v={`GPIO${bhv.sensor_pin} (ADC1)`} />}
          <Kv k="Threshold" v={bhv.sensor_source === "photoresistor_adc" ? `${Math.round(bhv.threshold)} counts` : `${bhv.threshold.toFixed(2)} (relative)`} />
          <Kv k="Dark means" v={bhv.dark_means.replace(/_/g, " ")} />
        </dl>
        <div>
          <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-dim">Pin map and connections (planned)</h4>
          <ul className="space-y-0.5 font-mono text-[11px] text-dim">
            {d.connections.map((c) => (
              <li key={c.id}>
                {pretty(c.src)} → {pretty(c.dst)} <span className="text-faint">· {c.kind}</span>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-dim">Deterministic validation</h4>
          <Results items={d.validation} />
        </div>
        <div>
          <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-dim">Tradeoffs</h4>
          <ul className="list-disc pl-4 text-dim">{d.tradeoffs.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
        <div className="flex gap-2">
          <Button variant="primary" busy={busy === "approve"} disabled={active || !!dead} onClick={approve}>
            {active ? "Approved" : "Approve design"}
          </Button>
          <Button onClick={() => run("reject", () => api.reject(pid, d.id), "Design rejected")}>Reject</Button>
          <Button onClick={() => run("revalidate", () => fetch(`/api/validation/${pid}/${d.id}`, { method: "POST" }).then((r) => r.json()), "Re-validated")}>Re-validate</Button>
        </div>
      </div>
      <Comparison view={view} />
    </div>
  );
}
