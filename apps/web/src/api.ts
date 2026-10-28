import type {
  AssemblyStep,
  ComponentInstance,
  DesignCandidate,
  Mode,
  Project,
  ProjectState,
  RevisionEvent,
  Stage,
  VerificationResult,
} from "./types";

export class ApiFail extends Error {
  constructor(
    public code: string,
    message: string,
    public hint = "",
  ) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiFail("backend_unreachable", "Cannot reach the BuildLoop backend.", "Start it with: scripts/dev.ps1 (or uvicorn buildloop.main:app --port 8000).");
  }
  if (!res.ok) {
    let body: { code?: string; message?: string; hint?: string; detail?: string } = {};
    try {
      body = await res.json();
    } catch {
      /* non-JSON error */
    }
    throw new ApiFail(body.code ?? `http_${res.status}`, body.message ?? body.detail ?? res.statusText, body.hint ?? "");
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  call<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });

export interface HardwareSnapshot {
  status: "disconnected" | "connected" | "lost" | "unsupported";
  simulated: boolean;
  info: Record<string, unknown>;
  last_heartbeat: string | null;
  latest: { at: string; source: string; adc: number | null; led_state: boolean | null } | null;
  authorized_design: string | null;
  avg_latency_ms: number | null;
}

export interface Comparison {
  original: ViewDesign | null;
  revised: ViewDesign | null;
  removed_components: string[];
  limitations: string[];
}
export interface ViewDesign {
  id: string;
  title: string;
  sensor: string;
  overall: string;
  validation: DesignCandidate["validation"];
  invalidated_reasons: string[];
  substitute_notes: string[];
  tradeoffs: string[];
  verification: VerificationResult | null;
}

export interface ProjectView {
  project: Project;
  components: ComponentInstance[];
  designs: DesignCandidate[];
  state: ProjectState;
  revisions: RevisionEvent[];
  comparison: Comparison;
  decision_trace: Record<string, unknown>[];
  hardware: HardwareSnapshot | null;
}

export interface ScanResult {
  detections: { concept: string; box: number[]; confidence: number | null }[];
  components: ComponentInstance[];
  errors: { concept: string; code: string; message: string }[];
  provider: string;
  live: boolean;
  frame: { width: number; height: number; orig_width: number; orig_height: number; resized: boolean; bytes_in: number; bytes_out: number; mean_luma: number; sharpness: number; warnings: string[]; usable: boolean } | null;
  diff: { added: ComponentInstance[]; removed: ComponentInstance[]; changed: string[] };
  invalidated: string[];
}

export interface PortInfo {
  device: string;
  description: string;
  hwid: string;
}

export const api = {
  health: () => call<{ status: string; version: string; meta_configured: boolean; boards: string[]; hardware: HardwareSnapshot | null }>("/api/health"),
  usage: () => call<{ live: { configured: boolean; segmentation: Record<string, number | null>; reasoning: Record<string, number | null> }; demo: Record<string, unknown> }>("/api/meta/usage"),
  projects: () => call<Project[]>("/api/projects"),
  createProject: (name: string, mode: Mode, goal?: string, board = "esp32-devkitc-v4") => post<Project>("/api/projects", { name, mode, goal, board }),
  view: (pid: string) => call<ProjectView>(`/api/projects/${pid}`),
  setStage: (pid: string, stage: Stage) => post<Project>(`/api/projects/${pid}/stage`, { stage }),
  scan: (pid: string, image: Blob) => {
    const fd = new FormData();
    fd.append("image", image, "frame.jpg");
    return call<ScanResult>(`/api/perception/${pid}/scan`, { method: "POST", body: fd });
  },
  demoFrame: () => fetch("/api/perception/demo-frame.jpg").then((r) => r.blob()),
  patchComponent: (pid: string, cid: string, patch: Record<string, unknown>) =>
    call<ComponentInstance>(`/api/inventory/${pid}/${cid}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }),
  discardComponent: (pid: string, cid: string) => call<{ diff: unknown }>(`/api/inventory/${pid}/${cid}`, { method: "DELETE" }),
  addComponent: (pid: string, type: string, name: string) => post<ComponentInstance>(`/api/inventory/${pid}`, { type, name }),
  plan: (pid: string, goal?: string) => post<DesignCandidate[]>(`/api/designs/${pid}/plan`, { goal }),
  approve: (pid: string, did: string) => post<AssemblyStep[]>(`/api/designs/${pid}/${did}/approve`),
  reject: (pid: string, did: string) => post<{ ok: boolean }>(`/api/designs/${pid}/${did}/reject`),
  confirmStep: (pid: string, did: string, index: number) => post<AssemblyStep[]>(`/api/assembly/${pid}/${did}/${index}/confirm`),
  ports: () => call<PortInfo[]>("/api/hardware/ports"),
  connect: (body: { port?: string; simulated?: boolean; project_id?: string; approve_open_port?: boolean; confirm_port?: string }) => post<{ info: Record<string, unknown>; simulated: boolean }>("/api/hardware/connect", body),
  disconnect: () => post<{ ok: boolean }>("/api/hardware/disconnect"),
  authorize: (pid: string) => post<{ authorized: string; simulated: boolean }>(`/api/hardware/${pid}/authorize`),
  stop: (pid: string) => post<{ ok: boolean }>(`/api/hardware/${pid}/stop`),
  simAmbient: (value: number) => post<{ ambient: number }>("/api/hardware/sim/ambient", { value }),
  testStart: (pid: string) => post<Record<string, unknown>>(`/api/verification/${pid}/photoresistor/start`),
  testPhase: (pid: string, label: string, expected_led: boolean, seconds = 2, optical: number[] = []) => post<{ label: string; samples: number }>(`/api/verification/${pid}/phase`, { label, expected_led, seconds, optical }),
  testFinish: (pid: string) => post<VerificationResult>(`/api/verification/${pid}/photoresistor/finish`),
  hardwareValidate: () => post<{ simulated: boolean; ok: boolean; actuated: boolean; checks: { id: string; label: string; status: string; detail: string }[] }>("/api/hardware/validate"),
  agent: (pid: string) => call<AgentView>(`/api/agent/${pid}`),
  diagnostics: () => call<Diagnostics>("/api/hardware/diagnostics"),
  metrics: () => call<{ summary: Record<string, { unit: string; by_provenance: Record<string, { count: number; mean: number; median: number; min: number; max: number }> }>; count: number }>("/api/metrics"),
  opticalCommand: (pid: string, on: boolean, phase: string) => post<{ led_reported: boolean | null; t: number; simulated: boolean }>(`/api/verification/${pid}/optical/command`, { on, phase }),
  opticalEvaluate: (pid: string, samples: { t: number; luma: number; phase: string }[]) => post<VerificationResult>(`/api/verification/${pid}/optical/evaluate`, { samples }),
  phoneStatus: (pid: string) => call<{ connected: boolean; last_seen_s_ago: number | null; has_snapshot: boolean }>(`/api/phone/${pid}/status`),
  phoneBrightness: (pid: string, value: number) => post<{ led: boolean | null; commanded: boolean; simulated: boolean }>(`/api/verification/${pid}/phone/brightness`, { value }),
  phoneFinish: (pid: string) => post<VerificationResult>(`/api/verification/${pid}/phone/finish`),
};

export interface AgentView {
  stage: string;
  allowed_next: string[];
  next_action: { kind: string; label: string; reason: string; tab: string | null; needs_human: boolean };
  pending_approvals: string[];
  blockers: string[];
  replan_triggers: string[];
  tools_available: string[];
  evidence_level: string | null;
  summary: string;
}

export interface Diagnostics {
  ports: { device: string; description: string; vid: string | null; pid: string | null; bridge: string | null; candidate_note: string; likely_board: boolean }[];
  opened_any_port: boolean;
  expected_protocol: { version: number; framing: string; baud: number; firmware: string; handshake: string; commands: string[] };
  required_board_profile: { id: string; name: string; led_pin: number; adc_pin: number };
  wiring: { from: string; to: string }[];
  connection: { status: string; last_heartbeat: string | null; last_telemetry: string | null; simulated: boolean };
  errors: { code: string; recovery: string }[];
  validation_sequence: string[];
}

export function wsUrl(path: string): string {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}${path}`;
}
