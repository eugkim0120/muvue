import { clampPan } from "../src/canvas/pan";

test("an axis whose content fits the view is pinned to zero", () => {
  expect(clampPan({ x: 50, y: -80 }, { w: 300, h: 200 }, { w: 400, h: 500 })).toEqual({ x: 0, y: 0 });
});

test("a taller-than-view axis can pan only between the top and the bottom edge", () => {
  expect(clampPan({ x: 0, y: 40 }, { w: 300, h: 2000 }, { w: 400, h: 500 })).toEqual({ x: 0, y: 0 });
  expect(clampPan({ x: 0, y: -5000 }, { w: 300, h: 2000 }, { w: 400, h: 500 })).toEqual({ x: 0, y: -1500 });
  expect(clampPan({ x: 0, y: -700 }, { w: 300, h: 2000 }, { w: 400, h: 500 })).toEqual({ x: 0, y: -700 });
});
