import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Badge, Button, Kv } from "./ui";

/** Read-only hardware readiness view. Never opens a serial port. */
export default function DiagnosticsPanel() {
  const q = useQuery({ queryKey: ["diagnostics"], queryFn: api.diagnostics, refetchInterval: 8000 });
  const d = q.data;
  return (
    <details className="rounded border border-line bg-bg p-3" open>
      <summary className="cursor-pointer select-none font-semibold">Hardware diagnostics <Badge tone="neutral">read-only · no port opened</Badge></summary>
      {!d ? (
        <p className="mt-2 text-faint">{q.isError ? "Diagnostics unavailable." : "Loading…"}</p>
      ) : (
        <div className="mt-2 space-y-3">
          <div>
            <div className="mb-1 flex items-center justify-between">
              <b className="text-dim">Serial ports ({d.ports.length})</b>
              <Button onClick={() => q.refetch()}>Rescan</Button>
            </div>
            {d.ports.length === 0 ? (
              <p className="text-warn">{d.errors.find((e) => e.code === "no_ports")?.recovery}</p>
            ) : (
              <ul className="space-y-1">
                {d.ports.map((p) => (
                  <li key={p.device} className="rounded border border-line p-2">
                    <div className="flex items-center gap-2">
                      <span className="font-mono">{p.device}</span>
                      {p.likely_board ? <Badge tone="PASS">candidate: {p.bridge}</Badge> : <Badge tone="neutral">not a known board bridge</Badge>}
                      <span className="font-mono text-[10px] text-faint">{p.vid ?? "----"}:{p.pid ?? "----"}</span>
                    </div>
                    <p className="text-faint">{p.description} — {p.candidate_note}</p>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-1 text-faint">A bridge match is a hint, not proof. Choose the port yourself; identity is confirmed only by the firmware handshake.</p>
          </div>
          <dl>
            <Kv k="Required board" v={d.required_board_profile.name} />
            <Kv k="Expected firmware" v={`${d.expected_protocol.firmware} · protocol v${d.expected_protocol.version} · ${d.expected_protocol.baud} baud`} />
            <Kv k="Connection" v={d.connection.status} />
            <Kv k="Last heartbeat" v={d.connection.last_heartbeat ? new Date(d.connection.last_heartbeat).toLocaleTimeString() : "–"} />
            <Kv k="Last telemetry" v={d.connection.last_telemetry ? new Date(d.connection.last_telemetry).toLocaleTimeString() : "–"} />
          </dl>
          <div>
            <b className="text-dim">Required wiring</b>
            <ul className="mt-1 font-mono text-[11px] text-dim">{d.wiring.map((w) => <li key={w.from}>{w.from} → {w.to}</li>)}</ul>
          </div>
          <div>
            <b className="text-dim">First physical validation sequence</b>
            <ol className="mt-1 list-decimal pl-4 text-dim">{d.validation_sequence.map((s) => <li key={s}>{s}</li>)}</ol>
          </div>
          {d.errors.filter((e) => e.code !== "no_ports").map((e) => <p key={e.code} role="alert" className="text-bad">{e.recovery}</p>)}
        </div>
      )}
    </details>
  );
}
