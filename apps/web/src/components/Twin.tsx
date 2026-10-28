import { useMemo, useRef, useState } from "react";
import type { ProjectView } from "../api";
import { useUi } from "../store";
import type { ComponentInstance, Connection, DesignCandidate } from "../types";
import { Empty, Panel } from "./ui";

interface Node {
  c: ComponentInstance;
  x: number;
  y: number;
  w: number;
  h: number;
  missing: boolean;
  board: boolean;
  pins: Record<string, { x: number; y: number }>;
}

const BOARD = new Set(["esp32", "arduino"]);
const STATUS_COLOR: Record<string, string> = { user_confirmed: "#3fb68b", detected: "#7aa2f7", uncertain: "#e3a83b", failed: "#f0616d", validated: "#3fb68b", physically_tested: "#4c8dff" };

function layout(view: ProjectView, design: DesignCandidate | undefined): { nodes: Node[]; conns: Connection[] } {
  const inv = view.components;
  const byId = new Map<string, ComponentInstance>();
  inv.forEach((c) => byId.set(c.id, c));
  design?.components.forEach((c) => !byId.has(c.id) && byId.set(c.id, c));
  const conns = design?.connections ?? [];
  const all = [...byId.values()].filter((c) => c.type !== "breadboard" && c.type !== "jumper_wire");

  const board = all.find((c) => BOARD.has(c.type));
  const boardPins = new Map<string, number>();
  if (board) {
    const order = (p: string) => (p === "3V3" ? 0 : p === "GND" ? 99 : Number(p.replace(/\D/g, "")) || 50);
    const names = new Set<string>();
    for (const cn of conns) for (const ref of [cn.src, cn.dst]) if (ref.startsWith(board.id + ".")) names.add(ref.split(".")[1]);
    [...names].sort((a, b) => order(a) - order(b)).forEach((n, i) => boardPins.set(n, i));
  }
  const nodes: Node[] = [];
  const bh = Math.max(120, 50 + boardPins.size * 30);
  if (board) {
    const pins: Node["pins"] = {};
    boardPins.forEach((i, n) => (pins[n] = { x: 170, y: 46 + i * 30 }));
    nodes.push({ c: board, x: 0, y: 0, w: 170, h: bh, missing: false, board: true, pins });
  }
  const others = all.filter((c) => c !== board);
  others.forEach((c, i) => {
    const x = 330 + (i % 2) * 190;
    const y = Math.floor(i / 2) * 100;
    const pins: Node["pins"] = {};
    const names = c.pins.length ? c.pins.map((p) => p.name) : ["a", "b"];
    pins[names[0]] = { x, y: y + 25 };
    pins[names[1] ?? names[0]] = { x: x + 120, y: y + 25 };
    nodes.push({ c, x, y, w: 120, h: 50, missing: c.id.endsWith("_missing") || c.id.startsWith("rled_missing") || c.id.startsWith("rdiv_missing"), board: false, pins });
  });
  return { nodes, conns };
}

export default function Twin({ view }: { view: ProjectView | undefined }) {
  const selected = useUi((s) => s.selectedId);
  const select = useUi((s) => s.select);
  const designId = useUi((s) => s.designId);
  const [manual, setManual] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);
  const ref = useRef<SVGSVGElement>(null);

  const design = useMemo(() => {
    if (!view) return undefined;
    return view.designs.find((d) => d.id === (designId ?? view.state.active_design_id)) ?? view.designs.at(-1);
  }, [view, designId]);
  const g = useMemo(() => (view ? layout(view, design) : null), [view, design]);

  const fit = useMemo(() => {
    if (!g || g.nodes.length === 0) return { x: -30, y: -30, w: 640, h: 420 };
    const x1 = Math.min(...g.nodes.map((n) => n.x)) - 40;
    const y1 = Math.min(...g.nodes.map((n) => n.y)) - 30;
    const x2 = Math.max(...g.nodes.map((n) => n.x + n.w)) + 40;
    const y2 = Math.max(...g.nodes.map((n) => n.y + n.h)) + 40;
    return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
  }, [g]);
  const vb = manual ?? fit;
  const setVb = (f: (v: typeof fit) => typeof fit) => setManual((m) => f(m ?? fit));

  if (!view || !g || g.nodes.length === 0) {
    return (
      <Panel title="Digital Twin">
        <Empty title="Digital twin is empty" hint="Components appear here once detected or added. Connections appear when a design is proposed. They are plans, not observations." />
      </Panel>
    );
  }
  const nodeById = new Map(g.nodes.map((n) => [n.c.id, n]));
  const pinPos = (ref: string) => {
    const [cid, pin] = ref.split(".");
    const n = nodeById.get(cid);
    if (!n) return null;
    const p = n.pins[pin] ?? Object.values(n.pins)[0];
    return p ? { x: p.x, y: p.y, n } : null;
  };

  const zoom = (f: number) => setVb((v) => ({ x: v.x + (v.w - v.w * f) / 2, y: v.y + (v.h - v.h * f) / 2, w: v.w * f, h: v.h * f }));

  return (
    <Panel
      title={`Digital Twin${design ? " · " + design.title : ""}`}
      actions={
        <>
          <button className="px-1 text-dim hover:text-ink" aria-label="Zoom in" onClick={() => zoom(0.8)}>＋</button>
          <button className="px-1 text-dim hover:text-ink" aria-label="Zoom out" onClick={() => zoom(1.25)}>－</button>
          <button className="px-1 text-xs text-dim hover:text-ink" onClick={() => setManual(null)}>fit</button>
        </>
      }
    >
      <svg
        ref={ref}
        className="size-full min-h-64 cursor-grab touch-none bg-bg active:cursor-grabbing"
        viewBox={`${vb.x} ${vb.y} ${vb.w} ${vb.h}`}
        role="img"
        aria-label="Digital twin schematic"
        onWheel={(e) => zoom(e.deltaY > 0 ? 1.1 : 0.9)}
        onPointerDown={(e) => {
          drag.current = { x: e.clientX, y: e.clientY };
          (e.target as Element).setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => {
          if (!drag.current || !ref.current) return;
          const r = ref.current.getBoundingClientRect();
          const dx = ((e.clientX - drag.current.x) / r.width) * vb.w;
          const dy = ((e.clientY - drag.current.y) / r.height) * vb.h;
          drag.current = { x: e.clientX, y: e.clientY };
          setVb((v) => ({ ...v, x: v.x - dx, y: v.y - dy }));
        }}
        onPointerUp={() => (drag.current = null)}
      >
        <defs>
          <pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">
            <path d="M20 0H0V20" fill="none" stroke="#171c25" strokeWidth="1" />
          </pattern>
        </defs>
        <rect x={vb.x - 2000} y={vb.y - 2000} width={5000} height={5000} fill="url(#grid)" />
        {g.conns.map((c) => {
          const a = pinPos(c.src);
          const b = pinPos(c.dst);
          if (!a || !b) return null;
          const col = c.kind === "ground" ? "#6b7688" : c.kind === "power" ? "#e3a83b" : "#4c8dff";
          const mid = (a.x + b.x) / 2;
          return (
            <g key={c.id}>
              <path d={`M${a.x} ${a.y} C${mid} ${a.y} ${mid} ${b.y} ${b.x} ${b.y}`} fill="none" stroke={col} strokeWidth={1.6} strokeDasharray="5 4" opacity={0.9}>
                <title>{`${c.src} → ${c.dst}\n${c.expected}\nstatus: ${c.status} (planned, not observed)`}</title>
              </path>
            </g>
          );
        })}
        {g.nodes.map((n) => {
          const sel = n.c.id === selected;
          const col = n.missing ? "#f0616d" : n.c.available ? STATUS_COLOR[n.c.status] ?? "#6b7688" : "#6b7688";
          return (
            <g key={n.c.id} onClick={() => select(n.c.id)} style={{ cursor: "pointer" }} opacity={n.c.available || n.missing ? 1 : 0.5}>
              <rect x={n.x} y={n.y} width={n.w} height={n.h} rx={5} fill="#11151c" stroke={col} strokeWidth={sel ? 2.5 : 1.2} strokeDasharray={n.missing || !n.c.available ? "5 4" : undefined} />
              <text x={n.x + 8} y={n.y + 16} fontSize={11} fill="#e7ebf2" fontWeight={600}>
                {n.c.name.length > 18 ? n.c.name.slice(0, 17) + "…" : n.c.name}
              </text>
              <text x={n.x + 8} y={n.y + 30} fontSize={9} fill={col} fontFamily="ui-monospace, monospace">
                {n.missing ? "MISSING" : !n.c.available ? "REMOVED" : n.c.status.toUpperCase()}
              </text>
              {n.c.properties.resistance_ohm !== undefined && !n.board && (
                <text x={n.x + 8} y={n.y + 44} fontSize={9} fill="#9aa5b6" fontFamily="ui-monospace, monospace">
                  {n.c.properties.resistance_ohm == null ? "R unknown" : `${n.c.properties.resistance_ohm} Ω`}
                </text>
              )}
              {Object.entries(n.pins).map(([name, p]) => (
                <g key={name}>
                  <circle cx={p.x} cy={p.y} r={3.5} fill="#0b0e13" stroke={col} />
                  <text x={n.board ? p.x - 8 : p.x + (p.x === n.x ? -6 : 6)} y={p.y + 3} fontSize={9} fill="#9aa5b6" textAnchor={n.board || p.x !== n.x ? (n.board ? "end" : "start") : "end"} fontFamily="ui-monospace, monospace">
                    {name}
                  </text>
                </g>
              ))}
            </g>
          );
        })}
        <text x={vb.x + 8} y={vb.y + vb.h - 8} fontSize={9} fill="#6b7688">
          Dashed lines are PLANNED connections from the active design. No wiring is observed or verified here.
        </text>
      </svg>
    </Panel>
  );
}
