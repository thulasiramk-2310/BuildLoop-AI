/** Coordinate mapping between original image pixels and the displayed (object-fit: contain) canvas. */

export interface Size {
  w: number;
  h: number;
}
export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Rectangle occupied by an image of `img` size inside a `box` with object-fit: contain. */
export function containRect(img: Size, box: Size): Rect {
  if (img.w <= 0 || img.h <= 0 || box.w <= 0 || box.h <= 0) return { x: 0, y: 0, w: 0, h: 0 };
  const s = Math.min(box.w / img.w, box.h / img.h);
  const w = img.w * s;
  const h = img.h * s;
  return { x: (box.w - w) / 2, y: (box.h - h) / 2, w, h };
}

export function imageToDisplay(px: { x: number; y: number }, img: Size, box: Size): { x: number; y: number } {
  const r = containRect(img, box);
  return { x: r.x + (px.x / img.w) * r.w, y: r.y + (px.y / img.h) * r.h };
}

export function displayToImage(pt: { x: number; y: number }, img: Size, box: Size): { x: number; y: number } | null {
  const r = containRect(img, box);
  if (pt.x < r.x || pt.y < r.y || pt.x > r.x + r.w || pt.y > r.y + r.h) return null;
  return { x: ((pt.x - r.x) / r.w) * img.w, y: ((pt.y - r.y) / r.h) * img.h };
}

/** Image-pixel bbox -> display rectangle. */
export function bboxToDisplay(b: { x1: number; y1: number; x2: number; y2: number }, img: Size, box: Size): Rect {
  const a = imageToDisplay({ x: b.x1, y: b.y1 }, img, box);
  const c = imageToDisplay({ x: b.x2, y: b.y2 }, img, box);
  return { x: a.x, y: a.y, w: c.x - a.x, h: c.y - a.y };
}
