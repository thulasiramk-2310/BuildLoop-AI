import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { Badge } from "./ui";

/** Measured observations with provenance. Empty until real runs happen; nothing is pre-filled. */
export default function MetricsPanel() {
  const q = useQuery({ queryKey: ["metrics"], queryFn: api.metrics, refetchInterval: 5000 });
  const entries = Object.entries(q.data?.summary ?? {});
  return (
    <div>
      <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-dim">Measurements ({q.data?.count ?? 0})</h3>
      {entries.length === 0 ? (
        <p className="text-faint">No measurements yet. Latency, token counts, validation outcomes and test timings appear here with live / simulated provenance.</p>
      ) : (
        <table className="w-full text-[11px]">
          <thead className="text-faint">
            <tr><th className="text-left font-normal">metric</th><th className="text-left font-normal">source</th><th className="text-right font-normal">n</th><th className="text-right font-normal">mean</th><th className="text-right font-normal">max</th></tr>
          </thead>
          <tbody className="font-mono">
            {entries.flatMap(([name, m]) =>
              Object.entries(m.by_provenance).map(([prov, v]) => (
                <tr key={name + prov} className="border-t border-line">
                  <td className="py-0.5">{name}</td>
                  <td><Badge tone={prov === "live" ? "real" : "simulated"}>{prov}</Badge></td>
                  <td className="text-right">{v.count}</td>
                  <td className="text-right">{v.mean} {m.unit}</td>
                  <td className="text-right">{v.max}</td>
                </tr>
              )),
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
