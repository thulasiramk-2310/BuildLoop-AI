import clsx from "clsx";
import type { ProjectView } from "../api";
import { Badge, Empty, Panel } from "./ui";

const KIND_TONE: Record<string, string> = {
  inventory_change: "WARNING",
  design_invalidated: "BLOCKED",
  design_created: "UNKNOWN",
  hardware_test: "PASS",
  stage: "neutral",
  decision: "neutral",
};

export default function Timeline({ view }: { view: ProjectView | undefined }) {
  const revs = view ? [...view.revisions].reverse() : [];
  return (
    <Panel title={`Revision Timeline · ${revs.length}`}>
      {revs.length === 0 ? (
        <Empty title="No history yet" hint="Inventory changes, design decisions, validations and hardware tests are recorded here and survive restarts." />
      ) : (
        <ol className="divide-y divide-line">
          {revs.map((r) => (
            <li key={r.id} className="flex gap-2 px-3 py-1.5 text-xs">
              <time className="w-14 shrink-0 font-mono text-[10px] text-faint" dateTime={r.at}>
                {new Date(r.at).toLocaleTimeString([], { hour12: false })}
              </time>
              <Badge tone={KIND_TONE[r.kind] ?? "neutral"}>{r.kind.replace("_", " ")}</Badge>
              <span className={clsx("min-w-0 flex-1", r.kind === "design_invalidated" && "text-bad")}>{r.summary}</span>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
