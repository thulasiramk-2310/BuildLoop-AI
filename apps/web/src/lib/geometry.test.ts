import { describe, expect, it } from "vitest";
import { bboxToDisplay, containRect, displayToImage, imageToDisplay } from "./geometry";

describe("geometry", () => {
  it("letterboxes a wide image inside a square box", () => {
    expect(containRect({ w: 1280, h: 720 }, { w: 640, h: 640 })).toEqual({ x: 0, y: 140, w: 640, h: 360 });
  });
  it("pillarboxes a tall image inside a wide box", () => {
    const r = containRect({ w: 100, h: 200 }, { w: 400, h: 200 });
    expect(r).toEqual({ x: 150, y: 0, w: 100, h: 200 });
  });
  it("maps image corners to the displayed image corners", () => {
    const img = { w: 1280, h: 720 };
    const box = { w: 640, h: 640 };
    expect(imageToDisplay({ x: 0, y: 0 }, img, box)).toEqual({ x: 0, y: 140 });
    expect(imageToDisplay({ x: 1280, y: 720 }, img, box)).toEqual({ x: 640, y: 500 });
  });
  it("round-trips display <-> image coordinates", () => {
    const img = { w: 1280, h: 720 };
    const box = { w: 800, h: 500 };
    const d = imageToDisplay({ x: 300, y: 200 }, img, box);
    const back = displayToImage(d, img, box)!;
    expect(back.x).toBeCloseTo(300);
    expect(back.y).toBeCloseTo(200);
  });
  it("returns null for clicks on letterbox bars", () => {
    expect(displayToImage({ x: 10, y: 10 }, { w: 1280, h: 720 }, { w: 640, h: 640 })).toBeNull();
  });
  it("scales bboxes", () => {
    const r = bboxToDisplay({ x1: 640, y1: 360, x2: 1280, y2: 720 }, { w: 1280, h: 720 }, { w: 640, h: 360 });
    expect(r).toEqual({ x: 320, y: 180, w: 320, h: 180 });
  });
});
