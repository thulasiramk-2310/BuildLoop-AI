import { describe, expect, it } from "vitest";
import { calibrate, meanLuma, smooth } from "./brightness";

describe("brightness", () => {
  it("computes luma extremes", () => {
    expect(meanLuma(new Uint8ClampedArray([0, 0, 0, 255, 0, 0, 0, 255]))).toBe(0);
    expect(meanLuma(new Uint8ClampedArray([255, 255, 255, 255]))).toBeCloseTo(1);
  });
  it("is zero for an empty buffer", () => {
    expect(meanLuma(new Uint8ClampedArray([]))).toBe(0);
  });
  it("requires calibration", () => {
    expect(calibrate(0.5, null)).toBeNull();
  });
  it("rejects degenerate calibration", () => {
    expect(calibrate(0.5, { dark: 0.4, bright: 0.41 })).toBeNull();
  });
  it("maps and clamps", () => {
    const cal = { dark: 0.1, bright: 0.7 };
    expect(calibrate(0.4, cal)).toBeCloseTo(0.5);
    expect(calibrate(0.0, cal)).toBe(0);
    expect(calibrate(0.9, cal)).toBe(1);
  });
  it("smooths", () => {
    expect(smooth(null, 0.8)).toBe(0.8);
    expect(smooth(0.2, 1.0, 0.5)).toBeCloseTo(0.6);
  });
});
