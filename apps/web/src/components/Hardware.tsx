import clsx from "clsx";
import QRCode from "qrcode";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, wsUrl } from "../api";
import type { ProjectView } from "../api";
import { useUi } from "../store";
import type { VerificationLevel, VerificationResult } from "../types";
import DiagnosticsPanel from "./Diagnostics";
import { Badge, Button, Empty, Kv, useAction } from "./ui";

interface Tel {
  at: string;
  adc: number | null;
  led_state: boolean | null;
  source: string;
}

function useTelemetry(connected: boolean) {
  const [rows, setRows] = useState<Tel[]>([]);
  useEffect(() => {
    if (!connected) return;
    const ws = new WebSocket(wsUrl("/ws/telemetry"));
    ws.onmessage = (e) => {
      const m = JSON.parse(e.data);
      if (m.type === "telemetry") setRows((r) => [...r.slice(-119), m as Tel]);
    };
    return () => ws.close();
  }, [connected]);
  return rows;
}

function Spark({ rows }: { rows: Tel[] }) {
  const pts = rows.filter((r) => r.adc != null);
  if (pts.length < 2) return <div className="h-14 rounded border border-line bg-bg" />;
  const path = pts.map((r, i) => `${i === 0 ? "M" : "L"}${(i / (pts.length - 1)) * 300} ${50 - ((r.adc ?? 0) / 4095) * 46}`).join(" ");
  return (
    <svg viewBox="0 0 300 54" className="h-14 w-full rounded border border-line bg-bg" role="img" aria-label="ADC history">
      <path d={path} fill="none" stroke="#4c8dff" strokeWidth="1.5" />
      {pts.map((r, i) => r.led_state && <rect key={i} x={(i / (pts.length - 1)) * 300} y={50} width={2.5} height={3} fill="#e3a83b" />)}
    </svg>
  );
}

const LADDER: { level: VerificationLevel; label: string }[] = [
  { level: "command_acknowledged", label: "Command acknowledged" },
  { level: "gpio_state_reported", label: "GPIO state reported" },
  { level: "sensor_response_observed", label: "Sensor response observed" },
  { level: "optical_output_observed", label: "Optical output observed (independent)" },
  { level: "physical_behavior_verified", label: "Physical behavior verified" },
];

function Ladder({ v }: { v: VerificationResult }) {
  const reached = LADDER.findIndex((l) => l.level === v.level);
  const sim = v.evidence.some((e) => e.simulated);
  return (
    <div className="rounded border border-line bg-bg p-3">
      <div className="mb-2 flex items-center gap-2">
        <b>Verification</b>
        <Badge tone={v.passed === true ? "PASS" : v.passed === false ? "BLOCKED" : "UNKNOWN"}>{v.passed === true ? "consistent" : v.passed === false ? "contradiction" : "inconclusive"}</Badge>
        {sim && <Badge tone="simulated">simulated device</Badge>}
      </div>
      <ol className="space-y-1">
        {LADDER.map((l, i) => (
          <li key={l.level} className={clsx("flex items-center gap-2", i <= reached ? "text-ink" : "text-faint")}>
            <span className={clsx("inline-block size-2 rounded-full", i <= reached ? (sim && i >= 2 ? "bg-warn" : "bg-ok") : "bg-line")} />
            {l.label}
          </li>
        ))}
      </ol>
      <p className="mt-2 text-dim">{v.summary}</p>
      {v.contradictions.length > 0 && <ul className="mt-1 list-disc pl-4 text-bad">{v.contradictions.map((c) => <li key={c}>{c}</li>)}</ul>}
      <ul className="mt-2 space-y-0.5 text-[11px] text-dim">
        {v.evidence.map((e, i) => <li key={i}>• {e.detail}{e.simulated ? " (simulated)" : ""}</li>)}
      </ul>
    </div>
  );
}

function PhonePairing({ pid }: { pid: string }) {
  const net = useQuery({ queryKey: ["net"], queryFn: () => fetch("/api/network").then((r) => r.json() as Promise<{ lan_ips: string[] }>) });
  const [host, setHost] = useState("");
  const canvas = useRef<HTMLCanvasElement>(null);
  const phone = useQuery({ queryKey: ["phone", pid], queryFn: () => api.phoneStatus(pid), refetchInterval: 1500 });
  const [snapTick, setSnapTick] = useState(0);
  useEffect(() => { if (phone.data?.has_snapshot) setSnapTick((t) => t + 1); }, [phone.data?.has_snapshot, phone.data?.last_seen_s_ago === null]);
  const ip = host || net.data?.lan_ips?.[0] || location.hostname;
  const url = `https://${ip}:${location.port || 5173}/phone?project=${pid}`;
  useEffect(() => {
    if (canvas.current) void QRCode.toCanvas(canvas.current, url, { width: 132, margin: 1, color: { dark: "#0b0e13", light: "#e7ebf2" } });
  }, [url]);
  return (
    <div className="flex gap-3 rounded border border-line bg-bg p-3">
      <canvas ref={canvas} aria-label="QR code to pair phone" />
      <div className="min-w-0 flex-1">
        <b>Pair phone camera</b>
        <p className="mt-1 text-dim">Phone and laptop on the same Wi-Fi. Scan, accept the self-signed certificate warning once, allow the camera.</p>
        <label className="mt-2 block text-dim">
          Laptop address
          <input value={host} placeholder={ip} onChange={(e) => setHost(e.target.value)} className="mt-0.5 h-6 w-full rounded border border-line bg-panel-2 px-1.5 font-mono text-xs" />
        </label>
        <p className="mt-1 break-all font-mono text-[10px] text-faint">{url}</p>
        <p className="mt-2"><Badge tone={phone.data?.connected ? "real" : "neutral"}>{phone.data?.connected ? "phone connected" : "phone not connected"}</Badge></p>
        {phone.data?.has_snapshot && <img key={snapTick} src={`/api/phone/${pid}/snapshot.jpg?t=${Date.now()}`} alt="Latest phone snapshot" className="mt-2 max-h-28 rounded border border-line" />}
      </div>
    </div>
  );
}

export default function Hardware({ view }: { view: ProjectView | undefined }) {
  const pid = useUi((s) => s.projectId);
  const { run, busy } = useAction();
  const ports = useQuery({ queryKey: ["ports"], queryFn: api.ports });
  const [port, setPort] = useState("");
  const [approvePort, setApprovePort] = useState(false);
  const [validation, setValidation] = useState<{ simulated: boolean; ok: boolean; checks: { id: string; label: string; status: string; detail: string }[] } | null>(null);
  const [confirmPort, setConfirmPort] = useState("");
  const [ack, setAck] = useState(false);
  const [ambient, setAmbient] = useState(0.9);
  const hw = view?.hardware ?? null;
  const connected = hw?.status === "connected";
  const rows = useTelemetry(connected);
  const [started, setStarted] = useState(false);

  if (!view || !pid) return <Empty title="No project selected" />;
  const demo = view.project.mode === "demo";
  const did = view.state.active_design_id;
  const design = view.designs.find((d) => d.id === did);
  const steps = did ? view.state.steps_by_design[did] ?? [] : [];
  const stepsDone = steps.length > 0 && steps.every((s) => s.confirmed);
  const authorized = !!hw?.authorized_design && hw.authorized_design === did;
  const ver = did ? view.state.verifications[did] : undefined;
  const latest = rows.at(-1) ?? hw?.latest ?? null;
  const isLdr = design?.behavior.sensor_source === "photoresistor_adc";

  return (
    <div className="space-y-3 p-3 text-xs">
      <DiagnosticsPanel />
      <div className="flex items-center gap-2">
        <Button variant="danger" onClick={() => run("stop", () => api.stop(pid), "Stopped. LED off, authorization revoked.")} disabled={!hw || hw.status === "disconnected"}>
          ■ Emergency stop
        </Button>
        <span className="text-faint">Turns the LED off and revokes all actuation permission.</span>
      </div>

      <div className="rounded border border-line bg-bg p-3">
        <div className="mb-2 flex items-center gap-2">
          <b>Device</b>
          <Badge tone={connected ? "real" : hw?.status === "lost" ? "BLOCKED" : "neutral"}>{hw?.status ?? "disconnected"}</Badge>
          {hw?.simulated && <Badge tone="simulated">SIMULATED — not physical hardware</Badge>}
        </div>
        {connected ? (
          <>
            <dl>
              <Kv k="Firmware" v={`${hw!.info.fw ?? "?"} ${hw!.info.fw_version ?? ""}`} />
              <Kv k="Board profile" v={String(hw!.info.board ?? "?")} />
              <Kv k="LED / ADC pin" v={`GPIO${hw!.info.led_pin} / GPIO${hw!.info.adc_pin}`} />
              <Kv k="Last heartbeat" v={hw!.last_heartbeat ? new Date(hw!.last_heartbeat).toLocaleTimeString() : "–"} />
              <Kv k="Avg command latency" v={hw!.avg_latency_ms != null ? `${hw!.avg_latency_ms.toFixed(1)} ms` : "–"} />
              <Kv k="Latest reading" v={latest ? `ADC ${latest.adc ?? "–"} · LED ${("led_state" in latest ? latest.led_state : null) ? "ON" : "OFF"} (${latest.source})` : "–"} />
            </dl>
            <Spark rows={rows} />
            <div className="mt-2 flex flex-wrap gap-2">
              <Button busy={busy === "val"} onClick={async () => setValidation((await run("val", () => api.hardwareValidate())) ?? null)}>Run first validation (no actuation)</Button>
              <Button onClick={() => run("disc", () => api.disconnect())}>Disconnect</Button>
            </div>
            {validation && (
              <ul className="mt-2 space-y-1">
                {validation.checks.map((c) => (
                  <li key={c.id} className="flex items-start gap-2">
                    <Badge tone={c.status === "pass" ? "PASS" : c.status === "fail" ? "BLOCKED" : c.status === "pending" ? "neutral" : "UNKNOWN"}>{c.status}</Badge>
                    <span><b className="text-ink">{c.label}</b> <span className="text-dim">— {c.detail}</span></span>
                  </li>
                ))}
                {validation.simulated && <li className="text-warn">Simulated device: this checklist is NOT hardware validation.</li>}
              </ul>
            )}
          </>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <label className="sr-only" htmlFor="port">Serial port</label>
            <select id="port" value={port} onChange={(e) => setPort(e.target.value)} className="h-7 max-w-64 rounded border border-line bg-panel-2 px-1.5">
              <option value="">Select serial port…</option>
              {ports.data?.map((p) => <option key={p.device} value={p.device}>{p.device} — {p.description}</option>)}
            </select>
            <Button onClick={() => ports.refetch()}>Rescan</Button>
            <label className="flex items-center gap-1.5 text-dim"><input type="checkbox" checked={approvePort} onChange={(e) => setApprovePort(e.target.checked)} /> I approve opening this port (may reset the board)</label>
            <label className="flex items-center gap-1.5 text-dim">type port name <input value={confirmPort} onChange={(e) => setConfirmPort(e.target.value.trim())} placeholder={port || "COMx"} className="h-6 w-20 rounded border border-line bg-bg px-1.5 font-mono" /></label>
            <Button variant="primary" disabled={!port || !approvePort || confirmPort !== port} busy={busy === "conn"} onClick={() => run("conn", () => api.connect({ port, approve_open_port: true, confirm_port: confirmPort }), "Connected")}>Connect</Button>
            {demo && <Button onClick={() => run("conn", () => api.connect({ simulated: true, project_id: pid }), "Simulated device connected")}>Use simulated device</Button>}
            {hw?.status === "lost" && <span className="text-bad">Connection lost. Reconnect.</span>}
            {ports.data?.length === 0 && <span className="text-faint">No serial ports found. Plug in the board and flash BuildLoop firmware.</span>}
          </div>
        )}
        <p className="mt-2 text-faint">Handshake requires BuildLoop firmware. Opening a port can reset the board. BuildLoop never flashes firmware on its own.</p>
      </div>

      {design && (
        <div className="rounded border border-line bg-bg p-3">
          <b>Execution gate</b>
          <ul className="mt-1 space-y-0.5 text-dim">
            <li>{did ? "✓" : "✗"} approved design: {design.title}</li>
            <li>{design.validation.every((v) => v.status === "PASS" || v.status === "WARNING") ? "✓" : "✗"} validation has no BLOCKED/UNKNOWN</li>
            <li>{stepsDone ? "✓" : "✗"} every assembly step confirmed by you</li>
            <li>{connected ? "✓" : "✗"} board connected {hw?.simulated ? "(simulated)" : ""}</li>
          </ul>
          <label className="mt-2 flex items-start gap-2 text-dim">
            <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" />
            I checked the wiring against the plan and approve powering and driving this low-voltage USB circuit.
          </label>
          <Button className="mt-2" variant="primary" disabled={!ack || !stepsDone || !connected || authorized} busy={busy === "auth"} onClick={() => run("auth", () => api.authorize(pid), "Execution authorized")}>
            {authorized ? "Authorized" : "Authorize execution"}
          </Button>
        </div>
      )}

      {authorized && design && (
        <div className="rounded border border-line bg-bg p-3">
          <b>Test</b>
          {hw?.simulated && isLdr && (
            <label className="mt-2 flex items-center gap-2 text-dim">
              Simulated ambient light
              <input type="range" min={0} max={1} step={0.05} value={ambient} onChange={(e) => { const v = Number(e.target.value); setAmbient(v); void api.simAmbient(v); }} />
              <span className="font-mono">{ambient.toFixed(2)}</span>
              <Badge tone="simulated">simulation control</Badge>
            </label>
          )}
          {isLdr ? (
            <div className="mt-2 flex flex-wrap gap-2">
              <Button busy={busy === "t1"} onClick={() => run("t1", async () => { await api.testStart(pid); setStarted(true); }, "On-device test running")}>1. Start on-device test</Button>
              <Button disabled={!started} busy={busy === "t2"} onClick={() => run("t2", () => api.testPhase(pid, "bright", false, 2), "Bright phase captured")}>2. Capture BRIGHT (LED should be off)</Button>
              <Button disabled={!started} busy={busy === "t3"} onClick={() => run("t3", () => api.testPhase(pid, "dark", true, 2), "Dark phase captured")}>3. Capture DARK — cover sensor (LED should be on)</Button>
              <Button variant="primary" disabled={!started} busy={busy === "t4"} onClick={() => run("t4", () => api.testFinish(pid))}>4. Evaluate</Button>
            </div>
          ) : (
            <div className="mt-2 space-y-2 text-dim">
              <p>The phone camera (or the laptop camera in the Workspace) streams calibrated brightness. The backend converts it to whitelisted LED commands.</p>
              <div className="flex flex-wrap items-center gap-2">
                <PhoneControls pid={pid} threshold={design.behavior.threshold} />
                <Button variant="primary" busy={busy === "pf"} onClick={() => run("pf", () => api.phoneFinish(pid))}>Evaluate</Button>
              </div>
            </div>
          )}
        </div>
      )}

      {ver && <Ladder v={ver} />}
      <PhonePairing pid={pid} />
    </div>
  );
}

/** Manual brightness probe for demos without a camera: sends one calibrated value. */
function PhoneControls({ pid, threshold }: { pid: string; threshold: number }) {
  const { run } = useAction();
  const [v, setV] = useState(0.8);
  const [led, setLed] = useState<boolean | null>(null);
  return (
    <>
      <label className="flex items-center gap-2">
        brightness
        <input type="range" min={0} max={1} step={0.05} value={v} onChange={(e) => setV(Number(e.target.value))} />
        <span className="font-mono">{v.toFixed(2)}</span>
      </label>
      <Button onClick={async () => { const r = await run("pb", () => api.phoneBrightness(pid, v)); if (r) setLed(r.led); }}>Send reading</Button>
      <span className="font-mono">threshold {threshold.toFixed(2)} · LED {led == null ? "?" : led ? "ON" : "OFF"}</span>
    </>
  );
}
