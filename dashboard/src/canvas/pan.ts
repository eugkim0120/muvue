type Pt = { x: number; y: number };
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function clampPan(pan: Pt, content: { w: number; h: number }, view: { w: number; h: number }): Pt {
  return {
    x: content.w <= view.w ? 0 : clamp(pan.x, view.w - content.w, 0),
    y: content.h <= view.h ? 0 : clamp(pan.y, view.h - content.h, 0),
  };
}
