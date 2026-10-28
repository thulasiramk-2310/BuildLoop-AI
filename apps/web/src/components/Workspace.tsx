import clsx from "clsx";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { calibrate, meanLuma, smooth, type Calibration } from "../lib/brightness";
import { displayToImage } from "../lib/geometry";
import { useUi } from "../store";
import type { ComponentInstance } from "../types";
import OpticalCheck from "./OpticalCheck";
import { Badge, Button, Panel, useAction } from "./ui";
import type { ProjectView } from "../api";

type CamState = "idle" | "requesting" | "live" | "denied" | "error";
const RES = { "640×480": [640, 480], "1280×720": [1280, 720], "1920×1080": [1920, 1080] } as const;

function statusColor(c: ComponentInstance) {
  if (!c.available) return "#6b7688";
  if (c.status === "user_confirmed") return "#3fb68b";
  if (c.status === "uncertain") return "#e3a83b";
  return "#7aa2f7";
}

export default function Workspace({ view }: { view: ProjectView | undefined }) {
  const pid = useUi((s) => s.projectId);
  const selected = useUi((s) => s.selectedId);
  const select = useUi((s) => s.select);
  const { run, busy } = useAction();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [cam, setCam] = useState<CamState>("idle");
  const [camMsg, setCamMsg] = useState("");
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState("");
  const [res, setRes] = useState<keyof typeof RES>("1280×720");
  const [frozen, setFrozen] = useState<{ url: string; blob: Blob; w: number; h: number; demo: boolean } | null>(null);
  const [confirmSend, setConfirmSend] = useState(false);
  const [roi, setRoi] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  const [drag, setDrag] = useState<{ x: number; y: number } | null>(null);
  const boxRef = useRef<HTMLDivElement>(null);

  const mode = view?.project.mode;
  const comps = view?.components ?? [];

  const stop = useCallback(() => {
    const v = videoRef.current;
    (v?.srcObject as MediaStream | null)?.getTracks().forEach((t) => t.stop());
    if (v) v.srcObject = null;
    setCam("idle");
  }, []);

  const start = useCallback(async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      setCam("error");
      setCamMsg("Camera API unavailable. Use https:// (or localhost) and a supported browser.");
      return;
    }
    setCam("requesting");
    try {
      const [w, h] = RES[res];
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { deviceId: deviceId ? { exact: deviceId } : undefined, width: { ideal: w }, height: { ideal: h }, frameRate: { ideal: 15 } },
        audio: false,
      });
      stop();
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      setDevices((await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput"));
      setCam("live");
      setCamMsg("");
    } catch (e) {
      const err = e as DOMException;
      if (err.name === "NotAllowedError") {
        setCam("denied");
        setCamMsg("Camera permission denied. Allow camera access in the browser site settings, then retry.");
      } else if (err.name === "NotFoundError") {
        setCam("error");
        setCamMsg("No camera found. Connect a webcam, or pair a phone (QR in the Hardware tab).");
      } else {
        setCam("error");
        setCamMsg(`Camera error: ${err.message}`);
      }
    }
  }, [deviceId, res, stop]);

  useEffect(() => stop, [stop]);

  const freeze = async () => {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return;
    const c = document.createElement("canvas");
    c.width = v.videoWidth;
    c.height = v.videoHeight;
    c.getContext("2d")!.drawImage(v, 0, 0);
    const blob = await new Promise<Blob>((r) => c.toBlob((b) => r(b!), "image/jpeg", 0.9));
    setFrozen({ url: URL.createObjectURL(blob), blob, w: c.width, h: c.height, demo: false });
    setRoi(null);
  };

  const loadDemo = async () => {
    const blob = await api.demoFrame();
    const bmp = await createImageBitmap(blob);
    setFrozen({ url: URL.createObjectURL(blob), blob, w: bmp.width, h: bmp.height, demo: true });
    setRoi(null);
  };

  const retake = () => {
    if (frozen) URL.revokeObjectURL(frozen.url);
    setFrozen(null);
    setRoi(null);
    setConfirmSend(false);
  };

  const applyRoi = async () => {
    if (!frozen || !roi) return;
    const bmp = await createImageBitmap(frozen.blob);
    const x = Math.max(0, Math.min(roi.x1, roi.x2));
    const y = Math.max(0, Math.min(roi.y1, roi.y2));
    const w = Math.abs(roi.x2 - roi.x1);
    const h = Math.abs(roi.y2 - roi.y1);
    if (w < 32 || h < 32) return;
    const c = document.createElement("canvas");
    c.width = Math.round(w);
    c.height = Math.round(h);
    c.getContext("2d")!.drawImage(bmp, x, y, w, h, 0, 0, c.width, c.height);
    const blob = await new Promise<Blob>((r) => c.toBlob((b) => r(b!), "image/jpeg", 0.9));
    setFrozen({ ...frozen, url: URL.createObjectURL(blob), blob, w: c.width, h: c.height });
    setRoi(null);
  };

  const doScan = async () => {
    if (!pid || !frozen) return;
    setConfirmSend(false);
    const r = await run("scan", () => api.scan(pid, frozen.blob), "Scan complete");
    if (r && r.errors.length) useUi.getState().notify({ kind: "error", text: `${r.errors.length} concept request(s) failed: ${r.errors[0].message}` });
    if (r?.frame?.warnings.length) useUi.getState().notify({ kind: "error", text: `Frame quality: ${r.frame.warnings[0]}`, hint: "Detections from a poor frame are less reliable. Retake if results look wrong." });
  };

  const onScanClick = () => {
    if (mode === "real" && !frozen?.demo) setConfirmSend(true);
    else void doScan();
  };

  // ROI drag in image coordinates
  const mediaSize = () => {
    if (frozen) return { w: frozen.w, h: frozen.h };
    const v = videoRef.current;
    return v && v.videoWidth ? { w: v.videoWidth, h: v.videoHeight } : null;
  };
  const toImg = (e: React.PointerEvent) => {
    const el = boxRef.current;
    const m = mediaSize();
    if (!el || !m) return null;
    const r = el.getBoundingClientRect();
    return displayToImage({ x: e.clientX - r.left, y: e.clientY - r.top }, m, { w: r.width, h: r.height });
  };
  const canDrag = !!frozen || cam === "live";
  const opticalRoi = !frozen && roi ? { x: Math.min(roi.x1, roi.x2), y: Math.min(roi.y1, roi.y2), w: Math.abs(roi.x2 - roi.x1), h: Math.abs(roi.y2 - roi.y1) } : null;

  const vbW = frozen?.w ?? comps.find((c) => c.bbox)?.bbox?.img_w ?? 1280;
  const vbH = frozen?.h ?? comps.find((c) => c.bbox)?.bbox?.img_h ?? 720;
  const showOverlay = !!frozen && comps.some((c) => c.bbox);
  const cleanDemoMismatch = mode === "real" && frozen?.demo;

  return (
    <Panel
      title="Live Workspace"
      actions={
        <>
          {frozen?.demo && <Badge tone="demo">synthetic frame</Badge>}
          {cam === "live" && !frozen && <Badge tone="real">camera live</Badge>}
        </>
      }
    >
      <div className="flex min-h-full flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-line p-2">
          {cam !== "live" ? (
            <Button variant="primary" onClick={start} busy={cam === "requesting"}>
              Start camera
            </Button>
          ) : (
            <Button onClick={stop}>Stop camera</Button>
          )}
          <label className="sr-only" htmlFor="cam-device">
            Camera device
          </label>
          <select id="cam-device" value={deviceId} onChange={(e) => setDeviceId(e.target.value)} className="h-7 max-w-40 rounded border border-line bg-panel-2 px-1.5 text-xs">
            <option value="">Default camera</option>
            {devices.map((d, i) => (
              <option key={d.deviceId} value={d.deviceId}>
                {d.label || `Camera ${i + 1}`}
              </option>
            ))}
          </select>
          <label className="sr-only" htmlFor="cam-res">
            Resolution
          </label>
          <select id="cam-res" value={res} onChange={(e) => setRes(e.target.value as keyof typeof RES)} className="h-7 rounded border border-line bg-panel-2 px-1.5 text-xs">
            {Object.keys(RES).map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
          <div className="ml-auto flex gap-2">
            {mode === "demo" && !frozen && <Button onClick={loadDemo}>Load synthetic demo frame</Button>}
            {!frozen ? (
              <Button onClick={freeze} disabled={cam !== "live"}>
                Freeze frame
              </Button>
            ) : (
              <Button onClick={retake}>Retake</Button>
            )}
            <Button variant="primary" onClick={onScanClick} disabled={!frozen || !pid || cleanDemoMismatch} busy={busy === "scan"}>
              Scan components
            </Button>
          </div>
        </div>

        {confirmSend && (
          <div role="alert" className="flex items-center gap-3 border-b border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
            <span>
              This frame will be uploaded to <b>Meta SAM 3.1</b> (one request per component concept, 7 total). Nothing is sent continuously.
            </span>
            <Button variant="primary" onClick={doScan}>
              Send frame
            </Button>
            <Button onClick={() => setConfirmSend(false)}>Cancel</Button>
          </div>
        )}

        <div ref={boxRef} className="relative min-h-56 flex-1 bg-black" onPointerDown={(e) => canDrag && !drag && ((p) => p && (setDrag(p), setRoi({ x1: p.x, y1: p.y, x2: p.x, y2: p.y })))(toImg(e))} onPointerMove={(e) => drag && ((p) => p && setRoi((r) => (r ? { ...r, x2: p.x, y2: p.y } : r)))(toImg(e))} onPointerUp={() => setDrag(null)}>
          <video ref={videoRef} playsInline muted className={clsx("absolute inset-0 size-full object-contain", frozen && "hidden")} />
          {frozen && <img src={frozen.url} alt="Frozen workspace frame" className="absolute inset-0 size-full select-none object-contain" draggable={false} />}
          {!frozen && cam !== "live" && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-6 text-center">
              <p className="text-sm text-dim">{camMsg || "No camera running."}</p>
              <p className="max-w-sm text-xs text-faint">{mode === "demo" ? "Demo Mode: start a camera or load the synthetic demo frame (not a real photo)." : "Start the laptop camera, or pair a phone from the Hardware tab."}</p>
            </div>
          )}
          {showOverlay && (
            <svg className="pointer-events-none absolute inset-0 size-full" viewBox={`0 0 ${vbW} ${vbH}`} preserveAspectRatio="xMidYMid meet" aria-label="Detection overlay">
              {comps.map((c) => {
                if (!c.bbox) return null;
                const b = c.bbox;
                const col = statusColor(c);
                const sel = c.id === selected;
                return (
                  <g key={c.id} opacity={c.available ? 1 : 0.5}>
                    {c.mask_path && c.mask_bounds && (
                      <path d={c.mask_path} transform={`translate(${c.mask_bounds[0]} ${c.mask_bounds[1]})`} fill={col} fillOpacity={0.22} />
                    )}
                    <rect x={b.x1} y={b.y1} width={b.x2 - b.x1} height={b.y2 - b.y1} fill="none" stroke={col} strokeWidth={sel ? 4 : 2} strokeDasharray={c.available ? undefined : "8 6"} vectorEffect="non-scaling-stroke" />
                    <g transform={`translate(${b.x1} ${Math.max(18, b.y1 - 6)})`}>
                      <text fontSize={Math.max(12, vbW / 70)} fill={col} fontFamily="ui-monospace, monospace" paintOrder="stroke" stroke="#000" strokeWidth={3}>
                        {c.name}
                        {c.confidence != null ? ` ${(c.confidence * 100).toFixed(0)}%` : ""}
                        {c.status === "uncertain" ? " ?" : ""}
                      </text>
                    </g>
                  </g>
                );
              })}
            </svg>
          )}
          {roi && mediaSize() && (
            <svg className="pointer-events-none absolute inset-0 size-full" viewBox={`0 0 ${mediaSize()!.w} ${mediaSize()!.h}`} preserveAspectRatio="xMidYMid meet" aria-label="Region of interest">
              <rect x={Math.min(roi.x1, roi.x2)} y={Math.min(roi.y1, roi.y2)} width={Math.abs(roi.x2 - roi.x1)} height={Math.abs(roi.y2 - roi.y1)} fill="#4c8dff" fillOpacity={0.1} stroke="#4c8dff" strokeWidth={2} strokeDasharray="6 4" vectorEffect="non-scaling-stroke" />
            </svg>
          )}
          {/* clickable hit-areas for component selection */}
          {showOverlay && !drag && (
            <svg className="absolute inset-0 size-full" viewBox={`0 0 ${vbW} ${vbH}`} preserveAspectRatio="xMidYMid meet" style={{ pointerEvents: roi ? "none" : "auto" }}>
              {comps.map((c) => c.bbox && <rect key={c.id} x={c.bbox.x1} y={c.bbox.y1} width={c.bbox.x2 - c.bbox.x1} height={c.bbox.y2 - c.bbox.y1} fill="transparent" onClick={() => select(c.id)} style={{ cursor: "pointer" }} />)}
            </svg>
          )}
        </div>

        <div className="flex items-center gap-2 border-t border-line px-3 py-1.5 text-xs text-dim">
          {frozen && roi && Math.abs(roi.x2 - roi.x1) > 32 ? (
            <>
              <span>Region selected.</span>
              <Button onClick={applyRoi}>Crop to region</Button>
              <Button onClick={() => setRoi(null)}>Clear</Button>
            </>
          ) : frozen ? (
            <span>Frozen {frozen.w}×{frozen.h}. Drag on the image to select a region of interest. Overlay colours: blue detected · amber uncertain · green confirmed · grey unavailable.</span>
          ) : (
            <span>{cam === "live" ? "Drag on the live video to box an LED for the optical check. Live preview stays local. Frames leave this machine only when you press Scan (Real Mode asks first)." : "Camera off."}</span>
          )}
        </div>
        <LightSensor view={view} cameraLive={cam === "live"} videoRef={videoRef} />
        <OpticalCheck view={view} videoRef={videoRef} roi={opticalRoi} cameraLive={cam === "live" && !frozen} />
      </div>
    </Panel>
  );
}

/** Laptop-camera brightness as the substitute light sensor (same logic as the paired phone page). */
function LightSensor({ view, cameraLive, videoRef }: { view: ProjectView | undefined; cameraLive: boolean; videoRef: React.RefObject<HTMLVideoElement | null> }) {
  const pid = useUi((s) => s.projectId);
  const { run } = useAction();
  const design = view?.designs.find((d) => d.id === view.state.active_design_id);
  const eligible = design?.behavior.sensor_source === "phone_camera_brightness" && !!view?.hardware?.authorized_design;
  const [raw, setRaw] = useState<number | null>(null);
  const [cal, setCal] = useState<Calibration | null>(null);
  const [dark, setDark] = useState<number | null>(null);
  const [bright, setBright] = useState<number | null>(null);
  const [sending, setSending] = useState(false);
  const canvas = useMemo(() => document.createElement("canvas"), []);
  const rawRef = useRef<number | null>(null);

  useEffect(() => {
    if (!cameraLive) return;
    const id = setInterval(() => {
      const v = videoRef.current;
      if (!v || !v.videoWidth) return;
      canvas.width = 32;
      canvas.height = 24;
      const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
      ctx.drawImage(v, 0, 0, 32, 24);
      const s = smooth(rawRef.current, meanLuma(ctx.getImageData(0, 0, 32, 24).data));
      rawRef.current = s;
      setRaw(s);
    }, 250);
    return () => clearInterval(id);
  }, [cameraLive, canvas, videoRef]);

  useEffect(() => {
    if (dark != null && bright != null) setCal({ dark, bright });
  }, [dark, bright]);

  const value = raw != null ? calibrate(raw, cal) : null;
  useEffect(() => {
    if (!sending || !pid || value == null) return;
    const id = setInterval(() => {
      if (rawRef.current == null) return;
      const v = calibrate(rawRef.current, cal);
      if (v != null) void api.phoneBrightness(pid, v).catch(() => setSending(false));
    }, 500);
    return () => clearInterval(id);
  }, [sending, pid, cal, value]);

  if (!eligible) return null;
  return (
    <div className="border-t border-line bg-panel-2 p-2 text-xs">
      <div className="mb-1 flex items-center gap-2">
        <b className="text-ink">Substitute light sensor: this camera</b>
        <Badge tone="neutral">relative brightness, not lux</Badge>
      </div>
      <p className="mb-2 text-dim">Measures what this camera sees, not the light at the LED. Calibrate with a dark and a bright view.</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => setDark(raw)} disabled={raw == null}>Set dark</Button>
        <Button onClick={() => setBright(raw)} disabled={raw == null}>Set bright</Button>
        <span className="font-mono">raw {raw?.toFixed(3) ?? "–"} → {value != null ? value.toFixed(2) : "uncalibrated"}</span>
        <Button variant="primary" disabled={value == null || !cameraLive} onClick={() => run("sensor", async () => setSending((s) => !s))}>
          {sending ? "Stop streaming" : "Stream to design"}
        </Button>
      </div>
    </div>
  );
}
