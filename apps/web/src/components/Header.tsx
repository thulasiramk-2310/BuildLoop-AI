import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { api } from "../api";
import { useUi } from "../store";
import type { Project, Stage } from "../types";
import { Badge, Button, useAction } from "./ui";

const NEXT: Record<Stage, string> = {
  OBSERVE: "Next: start a camera (or load the demo frame), freeze a frame and press Scan components.",
  INTERPRET: "Interpreting the scan…",
  CONFIRM: "Next: correct detections, enter resistor values and Confirm every component, then ask the Copilot to design.",
  PLAN: "Next: review the proposed designs in the Design tab.",
  VALIDATE: "Next: read the validation results, then approve one design.",
  GUIDE: "Next: follow the assembly steps; confirm each only after you have wired and checked it.",
  TEST: "Next: run the test and watch the evidence level. Use Emergency stop any time.",
  COMPARE: "Next: compare results. Remove or change a part to watch the design adapt, or finish.",
  REDESIGN: "Reality changed. Next: propose a redesign in the Copilot tab and review it before approving.",
  COMPLETE: "Complete. Review the revision timeline and the before/after comparison.",
};

const STAGES: Stage[] = ["OBSERVE", "INTERPRET", "CONFIRM", "PLAN", "VALIDATE", "GUIDE", "TEST", "COMPARE", "REDESIGN", "COMPLETE"];

export default function Header({ project }: { project: Project | undefined }) {
  const pid = useUi((s) => s.projectId);
  const setProject = useUi((s) => s.setProject);
  const { run, busy } = useAction();
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 5000 });
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const agent = useQuery({ queryKey: ["agent", pid, project?.stage], queryFn: () => api.agent(pid!), enabled: !!pid && !!project, refetchInterval: 2500 });

  const create = (mode: "demo" | "real") =>
    run(
      "create",
      async () => {
        const p = await api.createProject(`${mode === "demo" ? "Demo" : "Real"} build ${new Date().toLocaleTimeString()}`, mode);
        setProject(p.id);
        void projects.refetch();
      },
      `Created ${mode} project`,
    );

  const stageIdx = project ? STAGES.indexOf(project.stage) : -1;
  return (
    <header className="flex shrink-0 flex-col border-b border-line bg-panel">
      <div className="flex h-11 items-center gap-3 px-3">
        <div className="flex items-center gap-2">
          <svg width="20" height="20" viewBox="0 0 32 32" aria-hidden>
            <path d="M8 16a8 8 0 1 1 3 6.2" stroke="#4c8dff" strokeWidth="2.6" fill="none" strokeLinecap="round" />
            <path d="M8 22.5v-5h5" stroke="#4c8dff" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="text-sm font-semibold tracking-tight">BuildLoop</span>
          <span className="hidden text-xs text-faint md:inline">From physical reality to verified engineering</span>
        </div>
        <div className="mx-2 h-5 w-px bg-line" />
        <label className="sr-only" htmlFor="project-select">
          Project
        </label>
        <select
          id="project-select"
          value={pid ?? ""}
          onChange={(e) => setProject(e.target.value || null)}
          className="h-7 max-w-64 rounded border border-line bg-panel-2 px-2 text-xs"
        >
          <option value="">Select project…</option>
          {projects.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} ({p.mode})
            </option>
          ))}
        </select>
        <Button onClick={() => create("demo")} busy={busy === "create"}>
          New Demo project
        </Button>
        <Button onClick={() => create("real")} busy={busy === "create"} title="Uses live Meta API and real hardware">
          New Real project
        </Button>
        <div className="ml-auto flex items-center gap-2">
          {project && (
            <Badge tone={project.mode} title={project.mode === "demo" ? "Recorded/synthetic data and simulated hardware. Not physical evidence." : "Live Meta API and connected hardware only."}>
              {project.mode === "demo" ? "DEMO MODE · simulated" : "REAL MODE"}
            </Badge>
          )}
          <Badge tone={health.data ? (health.data.meta_configured ? "real" : "neutral") : "failed"} title="Meta API credentials are held by the backend only">
            {health.isError ? "backend offline" : health.data?.meta_configured ? "Meta API key set" : "no Meta key"}
          </Badge>
        </div>
      </div>
      <nav aria-label="Engineering loop stage" className="flex h-7 items-stretch overflow-x-auto border-t border-line px-3">
        {STAGES.map((s, i) => (
          <div
            key={s}
            aria-current={project?.stage === s ? "step" : undefined}
            className={clsx(
              "flex items-center gap-1.5 whitespace-nowrap border-b-2 px-2.5 text-[10px] font-semibold uppercase tracking-wider",
              project?.stage === s ? "border-accent text-accent" : i < stageIdx ? "border-transparent text-dim" : "border-transparent text-faint",
              s === "REDESIGN" && project?.stage === s && "border-warn text-warn",
            )}
          >
            <span className="font-mono text-[9px] opacity-60">{String(i + 1).padStart(2, "0")}</span>
            {s}
          </div>
        ))}
      </nav>
      {project && (
        <div role="status" className={clsx("flex flex-wrap items-center gap-x-3 gap-y-0.5 border-t border-line px-3 py-1 text-[11px]", agent.data?.replan_triggers.length ? "bg-warn/10 text-warn" : "bg-panel-2 text-dim")}>
          <span className="font-semibold uppercase tracking-wider text-accent">Agent</span>
          <span>
            <b className="text-ink">{agent.data?.next_action.label ?? NEXT[project.stage]}</b>
            {agent.data && <span> — {agent.data.next_action.reason}</span>}
          </span>
          {agent.data && agent.data.pending_approvals.length > 0 && <span className="text-faint">needs you: {agent.data.pending_approvals[0]}</span>}
          {agent.data && agent.data.blockers.length > 0 && <span className="text-bad">blocked: {agent.data.blockers[0]}</span>}
        </div>
      )}
    </header>
  );
}
