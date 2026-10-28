import { useRef, useState, type RefObject } from "react";
import { api } from "../api";
import type { ProjectView } from "../api";
import { meanLuma } from "../lib/brightness";
import { useUi } from "../store";
import { Badge, Button, useAction } from "./ui";

export interface Roi {
  x: number;
  y: number;
  w: number;
  h: number; // video pixel coordinates
}

const SAMPLE_MS = 200;
const PHASE_MS = 1800;
const SETTLE_MS = 400; // ignore samples right after a state change (LED rise + camera auto-exposure)

/**
 * Independent optical evidence: the camera watches a user-selected LED region while the backend commands OFF, ON, OFF.
 * Reports visible brightness change only. It does not prove wiring, polarity or electrical safety.
 */
export default function OpticalCheck({ view, videoRef, roi, cameraLive }: { view: ProjectView | undefined; videoRef: RefObject<HTMLVideoElement | null>; roi: Roi | null; cameraLive: boolean }) {
  const pid = useUi((s) => s.projectId);
  const { run } = useAction();
  const [log, setLog] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const authorized = !!view?.hardware?.authorized_design && view.hardware.authorized_design === view.state.active_design_id;
  const ver = view?.state.active_design_id ? view.state.verifications[view.state.active_design_id] : undefined;
  const opt = ver?.evidence.find((e) => e.kind === "optical" || e.kind === "optical_uncertain");

  if (!authorized) return null;

  const sampleRoi = (): number | null => {
    const v = videoRef.current;
    if (!v || !roi || !v.videoWidth) return null;
    const c = (canvas.current ??= document.createElement("canvas"));
    c.width = Math.max(4, Math.round(roi.w));
    c.height = Math.max(4, Math.round(roi.h));
    const ctx = c.getContext("2d", { willReadFrequently: true })!;
    ctx.drawImage(v, roi.x, roi.y, roi.w, roi.h, 0, 0, c.width, c.height);
    return meanLuma(ctx.getImageData(0, 0, c.width, c.height).data);
  };

  const go = async () => {
    if (!pid || !roi) return;
    setRunning(true);
    setLog(["Lock camera exposure/focus if you can. Hold the camera still."]);
    const samples: { t: number; luma: number; phase: string }[] = [];
    try {
      for (const [phase, on] of [["off_before", false], ["on", true], ["off_after", false]] as const) {
        const r = await api.opticalCommand(pid, on, phase);
        setLog((l) => [...l, `${phase}: commanded ${on ? "ON" : "OFF"}, device reported ${r.led_reported ? "ON" : "OFF"}${r.simulated ? " (simulated)" : ""}`]);
        const start = performance.now();
        while (performance.now() - start < PHASE_MS) {
          await new Promise((res) => setTimeout(res, SAMPLE_MS));
          if (performance.now() - start < SETTLE_MS) continue;
          const l = sampleRoi();
          if (l != null) samples.push({ t: (performance.now() - start) / 1000, luma: l, phase });
        }
      }
      await run("optical", () => api.opticalEvaluate(pid, samples), "Optical check evaluated");
    } catch (e) {
      setLog((l) => [...l, `Error: ${(e as Error).message}`]);
      void api.stop(pid).catch(() => undefined);
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="border-t border-line bg-panel-2 p-2 text-xs">
      <div className="mb-1 flex items-center gap-2">
        <b>Optical LED check</b>
        <Badge tone="neutral">visible change only</Badge>
        {view?.hardware?.simulated && <Badge tone="simulated">simulated LED: result is not physical evidence</Badge>}
      </div>
      <p className="mb-2 text-dim">Drag on the live video to box the LED. The backend commands OFF → ON → OFF while the camera records the region.</p>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-dim">{roi ? `ROI ${Math.round(roi.w)}×${Math.round(roi.h)} px` : "no region selected"}</span>
        <Button variant="primary" disabled={!roi || !cameraLive || running} busy={running} onClick={go}>Run optical check</Button>
      </div>
      {log.length > 0 && <ul className="mt-1 font-mono text-[11px] text-dim">{log.map((l, i) => <li key={i}>{l}</li>)}</ul>}
      {opt && <p className={opt.kind === "optical_uncertain" ? "mt-1 text-warn" : "mt-1 text-ink"}>{opt.detail}</p>}
    </div>
  );
}
