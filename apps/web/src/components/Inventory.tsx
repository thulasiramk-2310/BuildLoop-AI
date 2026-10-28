import clsx from "clsx";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { ProjectView } from "../api";
import { useUi } from "../store";
import type { ComponentInstance, ComponentType } from "../types";
import { Badge, Button, Empty, Panel, useAction } from "./ui";

const TYPES: ComponentType[] = ["esp32", "arduino", "breadboard", "led", "resistor", "pushbutton", "photoresistor", "jumper_wire", "unknown"];

function ValueField({ c, k, label, unit }: { c: ComponentInstance; k: string; label: string; unit: string }) {
  const pid = useUi((s) => s.projectId)!;
  const { run } = useAction();
  const cur = c.properties[k];
  const [v, setV] = useState(cur == null ? "" : String(cur));
  useEffect(() => setV(cur == null ? "" : String(cur)), [cur]);
  const commit = () => {
    const n = v.trim() === "" ? null : Number(v);
    if (n !== null && (!Number.isFinite(n) || n <= 0)) return;
    if (n === (cur ?? null)) return;
    void run("patch", () => api.patchComponent(pid, c.id, { properties: { [k]: n }, confirm: true }));
  };
  return (
    <label className="flex items-center gap-1.5 text-xs text-dim">
      {label}
      <input
        value={v}
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
        inputMode="decimal"
        placeholder="unknown"
        className={clsx("h-6 w-20 rounded border bg-bg px-1.5 font-mono text-xs", cur == null ? "border-warn/60" : "border-line")}
      />
      {unit}
    </label>
  );
}

export default function Inventory({ view }: { view: ProjectView | undefined }) {
  const pid = useUi((s) => s.projectId);
  const selected = useUi((s) => s.selectedId);
  const select = useUi((s) => s.select);
  const { run, busy } = useAction();
  const [newType, setNewType] = useState<ComponentType>("resistor");
  const comps = view?.components ?? [];

  return (
    <Panel
      title={`Inventory · ${comps.filter((c) => c.available).length} available`}
      actions={
        <>
          <label className="sr-only" htmlFor="new-type">
            Component type
          </label>
          <select id="new-type" value={newType} onChange={(e) => setNewType(e.target.value as ComponentType)} className="h-6 rounded border border-line bg-panel-2 px-1 text-xs">
            {TYPES.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          <Button disabled={!pid} onClick={() => pid && run("add", () => api.addComponent(pid, newType, newType.replace("_", " ")))}>
            Add
          </Button>
        </>
      }
    >
      {comps.length === 0 ? (
        <Empty title="No components yet" hint="Scan the workspace, or add components manually. Nothing is assumed until detected or added." />
      ) : (
        <ul className="divide-y divide-line">
          {comps.map((c) => (
            <li key={c.id} className={clsx("px-3 py-2", c.id === selected && "bg-panel-2", !c.available && "opacity-60")}>
              <div className="flex items-center gap-2">
                <button className="flex-1 truncate text-left text-xs font-medium hover:text-accent" onClick={() => select(c.id)} aria-pressed={c.id === selected}>
                  {c.name}
                </button>
                <select
                  aria-label={`Type of ${c.name}`}
                  value={c.type}
                  onChange={(e) => pid && run("patch", () => api.patchComponent(pid, c.id, { type: e.target.value }))}
                  className="h-6 rounded border border-line bg-bg px-1 text-xs"
                >
                  {TYPES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
                <Badge tone={c.available ? c.status : "neutral"}>{c.available ? c.status.replace("_", " ") : "removed"}</Badge>
              </div>
              <div className="mt-1.5 flex flex-wrap items-center gap-3">
                {c.type === "resistor" && <ValueField c={c} k="resistance_ohm" label="R" unit="Ω" />}
                {c.type === "led" && <ValueField c={c} k="forward_v" label="Vf" unit="V" />}
                {c.confidence != null && <span className="font-mono text-[10px] text-faint">conf {(c.confidence * 100).toFixed(0)}%</span>}
                <span className="ml-auto flex gap-1.5">
                  {c.available && c.status !== "user_confirmed" && (
                    <Button busy={busy === "patch"} onClick={() => pid && run("patch", () => api.patchComponent(pid, c.id, { confirm: true }))}>
                      Confirm
                    </Button>
                  )}
                  <Button variant={c.available ? "danger" : "ghost"} onClick={() => pid && run("patch", () => api.patchComponent(pid, c.id, { available: !c.available }), c.available ? `${c.name} marked unavailable` : `${c.name} restored`)}>
                    {c.available ? "Remove / unavailable" : "Restore"}
                  </Button>
                </span>
              </div>
              {c.id === selected && (
                <div className="mt-1.5 flex items-center gap-2">
                  <label className="flex items-center gap-1.5 text-xs text-dim">
                    Name
                    <input
                      key={c.name}
                      defaultValue={c.name}
                      onBlur={(e) => e.target.value.trim() && e.target.value !== c.name && pid && run("patch", () => api.patchComponent(pid, c.id, { name: e.target.value.trim() }))}
                      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                      className="h-6 w-40 rounded border border-line bg-bg px-1.5 text-xs"
                    />
                  </label>
                  {c.status !== "user_confirmed" && (
                    <Button variant="danger" onClick={() => pid && run("discard", () => api.discardComponent(pid, c.id), `${c.name} discarded (false detection)`)}>Discard false detection</Button>
                  )}
                </div>
              )}
              {c.type === "resistor" && c.properties.resistance_ohm == null && c.available && <p className="mt-1 text-[11px] text-warn">Unknown resistance. Read the colour bands or measure, then enter the value.</p>}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
