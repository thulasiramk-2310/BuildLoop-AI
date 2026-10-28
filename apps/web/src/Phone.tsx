import { useEffect, useRef, useState } from "react";
import { api, wsUrl } from "./api";
import { calibrate, meanLuma, smooth, type Calibration } from "./lib/brightness";

/** Phone companion page: local camera preview + calibrated brightness stream to the laptop backend. */
export default function Phone() {
  const project = new URLSearchParams(location.search).get("project");
  const videoRef = useRef<HTMLVideoElement>(null);
  const [status, setStatus] = useState("Tap Start to allow the camera.");
  const [raw, setRaw] = useState<number | null>(null);
  const [dark, setDark] = useState<number | null>(null);
  const [bright, setBright] = useState<number | null>(null);
  const [led, setLed] = useState<boolean | null>(null);
  const [sending, setSending] = useState(false);
  const [msg, setMsg] = useState("");
  const rawRef = useRef<number | null>(null);
  const cal: Calibration | null = dark != null && bright != null ? { dark, bright } : null;
  const value = raw != null ? calibrate(raw, cal) : null;

  const start = async () => {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 } }, audio: false });
      videoRef.current!.srcObject = s;
      await videoRef.current!.play();
      setStatus("Camera live. Frames stay on this phone unless you tap Scan.");
    } catch (e) {
      setStatus(`Camera unavailable: ${(e as Error).message}. This page needs https and camera permission.`);
    }
  };

  useEffect(() => {
    const c = document.createElement("canvas");
    c.width = 32;
    c.height = 24;
    const ctx = c.getContext("2d", { willReadFrequently: true })!;
    const id = setInterval(() => {
      const v = videoRef.current;
      if (!v || !v.videoWidth) return;
      ctx.drawImage(v, 0, 0, 32, 24);
      rawRef.current = smooth(rawRef.current, meanLuma(ctx.getImageData(0, 0, 32, 24).data));
      setRaw(rawRef.current);
    }, 250);
    return () => clearInterval(id);
  }, []);

  const [link, setLink] = useState<"connecting" | "online" | "offline">("connecting");
  const sendingRef = useRef(false);
  const calRef = useRef<Calibration | null>(null);
  sendingRef.current = sending;
  calRef.current = cal;

  // One persistent socket: pings keep the laptop's phone indicator alive; reconnects with backoff after drops.
  useEffect(() => {
    if (!project) return;
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;
    let retry = 0;
    let closed = false;
    const open = () => {
      setLink("connecting");
      ws = new WebSocket(wsUrl(`/ws/phone/${project}`));
      ws.onopen = () => {
        retry = 0;
        setLink("online");
      };
      ws.onmessage = (e) => {
        const m = JSON.parse(e.data);
        if (m.type === "state") setLed(m.led);
        if (m.type === "error") setMsg(m.message);
      };
      ws.onclose = () => {
        setLink("offline");
        if (!closed) setTimeout(open, Math.min(8000, 500 * 2 ** retry++));
      };
    };
    open();
    timer = setInterval(() => {
      if (ws?.readyState !== WebSocket.OPEN) return;
      const v = sendingRef.current && rawRef.current != null ? calibrate(rawRef.current, calRef.current) : null;
      ws.send(JSON.stringify(v != null ? { brightness: v } : { ping: true }));
    }, 500);
    return () => {
      closed = true;
      if (timer) clearInterval(timer);
      ws?.close();
    };
  }, [project]);

  /** Capture the current frame, downscaled to at most 1280 px on the long side to keep Wi-Fi uploads small. */
  const capture = async (): Promise<Blob | null> => {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return null;
    const s = Math.min(1, 1280 / Math.max(v.videoWidth, v.videoHeight));
    const c = document.createElement("canvas");
    c.width = Math.round(v.videoWidth * s);
    c.height = Math.round(v.videoHeight * s);
    c.getContext("2d")!.drawImage(v, 0, 0, c.width, c.height);
    return new Promise<Blob | null>((r) => c.toBlob((b) => r(b), "image/jpeg", 0.85));
  };

  const snapshot = async () => {
    const blob = await capture();
    if (!blob || !project) return;
    const fd = new FormData();
    fd.append("image", blob, "snap.jpg");
    const r = await fetch(`/api/phone/${project}/snapshot`, { method: "POST", body: fd });
    setMsg(r.ok ? "Snapshot sent to the laptop preview (not sent to Meta)." : "Snapshot failed.");
  };

  const scan = async () => {
    const blob = await capture();
    if (!blob || !project) return;
    try {
      const r = await api.scan(project, blob);
      const q = r.frame?.warnings.length ? ` Quality: ${r.frame.warnings[0]}` : "";
      setMsg(`Scan sent to laptop (${r.live ? "live " + r.provider : "demo fixture"}): ${r.components.length} components, all UNCONFIRMED until you confirm them on the laptop.${q}`);
    } catch (e) {
      setMsg((e as Error).message);
    }
  };

  if (!project) return <p className="p-4">Open this page from the QR code in BuildLoop (missing ?project=).</p>;
  const btn = "h-11 rounded border border-line bg-panel-2 px-3 text-sm disabled:opacity-40";
  return (
    <main className="mx-auto flex max-w-md flex-col gap-3 p-4">
      <h1 className="text-base font-semibold">BuildLoop phone camera</h1>
      <p className="text-xs text-dim">{status}</p>
      <video ref={videoRef} playsInline muted className="aspect-[4/3] w-full rounded border border-line bg-black object-cover" />
      <p className="text-xs">Laptop link: <b className={link === "online" ? "text-ok" : link === "offline" ? "text-bad" : "text-warn"}>{link}</b>{link === "offline" && " — retrying…"}</p>
      <p className="font-mono text-sm">raw {raw?.toFixed(3) ?? "–"} → {value != null ? value.toFixed(2) : "uncalibrated"} (camera brightness estimate, relative, NOT lux)</p>
      <div className="grid grid-cols-2 gap-2">
        <button className={btn} onClick={start}>Start camera</button>
        <button className={btn} onClick={snapshot} disabled={raw == null}>Send snapshot to laptop</button>
        <button className={btn} onClick={scan} disabled={raw == null}>Scan this view (to SAM)</button>
        <button className={btn} onClick={() => setDark(raw)} disabled={raw == null}>Set DARK</button>
        <button className={btn} onClick={() => setBright(raw)} disabled={raw == null}>Set BRIGHT</button>
      </div>
      <button className={`${btn} border-accent text-accent`} disabled={value == null} onClick={() => setSending((s) => !s)}>
        {sending ? "Stop streaming" : "Stream brightness to LED design"}
      </button>
      <p className="text-sm">LED: {led == null ? "–" : led ? "ON" : "OFF"} <span className="text-xs text-dim">(device-reported, not optically verified)</span></p>
      {msg && <p role="status" className="text-xs text-warn">{msg}</p>}
    </main>
  );
}
