/** Camera brightness as a software light sensor. Output is relative (0-1) after calibration, NOT lux. */

/** Rec. 709 luma of an RGBA buffer, averaged, normalized 0-1. */
export function meanLuma(rgba: Uint8ClampedArray | Uint8Array): number {
  const n = Math.floor(rgba.length / 4);
  if (n === 0) return 0;
  let sum = 0;
  for (let i = 0; i < n; i++) {
    const o = i * 4;
    sum += 0.2126 * rgba[o] + 0.7152 * rgba[o + 1] + 0.0722 * rgba[o + 2];
  }
  return sum / n / 255;
}

export interface Calibration {
  dark: number; // raw luma observed in the dark reference
  bright: number; // raw luma observed in the bright reference
}

/** Map raw luma to 0-1 using a two-point calibration. Returns null if the references are too close. */
export function calibrate(raw: number, cal: Calibration | null): number | null {
  if (!cal) return null;
  const span = cal.bright - cal.dark;
  if (span < 0.03) return null;
  return Math.min(1, Math.max(0, (raw - cal.dark) / span));
}

/** Exponential smoothing to reject auto-exposure flicker. */
export function smooth(prev: number | null, next: number, alpha = 0.4): number {
  return prev === null ? next : prev + alpha * (next - prev);
}
