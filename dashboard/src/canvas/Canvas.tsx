import { useLayoutEffect, useRef, useState } from "preact/hooks";
import { collapseDone, COLLAPSE_DONE_OVER, type CanvasData } from "./canvasData";
import { clampPan } from "./pan";
import { computeLayout, PLACEHOLDER_ID, type Placed } from "./flowLayout";
import { TaskBox } from "./TaskBox";
import { SpecRoot } from "./SpecRoot";
import { DagPlaceholder } from "./DagPlaceholder";
import { launches, planningNodeIds, type Activity } from "../project/activity";

const MIN_ZOOM = 0.3, MAX_ZOOM = 2;
const clampZoom = (z: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));

function boxStyle(p: Placed): Record<string, string> {
  return { position: "absolute", left: p.x + "px", top: p.y + "px", width: p.w + "px", height: p.h + "px" };
}

type Point = { x: number; y: number };
const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);

export function Canvas({ data: fullData, projectId, projectPhase, needsYou, activity }: { data: CanvasData; projectId: number; projectPhase: string; needsYou: Set<number>; activity: Activity | null }) {
  const [showDone, setShowDone] = useState(false);
  const { data, hiddenDone } = collapseDone(fullData, showDone);
  const planning = data.spec ? planningNodeIds(activity, launches.value, projectId).has(data.spec.id) : false;
  const lay = computeLayout(data.spec, data.tasks, data.edges, data.tasks.length === 0 && !!data.spec && data.spec.status !== "pending");

  const viewportRef = useRef<HTMLDivElement>(null);
  const [viewportWidth, setViewportWidth] = useState<number | null>(null);
  const [viewportHeight, setViewportHeight] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = viewportRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    setViewportWidth(el.clientWidth);
    setViewportHeight(el.clientHeight);
    const ro = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setViewportWidth(entry.contentRect.width);
        setViewportHeight(entry.contentRect.height);
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const fit = Math.min(1, (viewportWidth ?? lay.width) / lay.width);
  const [zoom, setZoom] = useState<number | null>(null);
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  const z = zoom ?? fit;
  const zoomedIn = z > fit + 0.001;
  // A layout narrower than the viewport sits centered rather than hugging the left edge.
  const centerX = viewportWidth !== null ? Math.max(0, (viewportWidth - lay.width * z) / 2) : 0;

  const content = { w: lay.width * z, h: lay.height * z };
  const view = { w: viewportWidth ?? content.w, h: viewportHeight ?? content.h };
  const tallerThanView = content.h > view.h + 1;
  const canPan = zoomedIn || tallerThanView;
  const setPanClamped = (p: Point) => setPan(clampPan(p, content, view));

  const pointers = useRef(new Map<number, Point>());
  const dragging = useRef<Point | null>(null);
  const pinch = useRef<{ startDistance: number; startZoom: number } | null>(null);

  function onPointerDown(e: PointerEvent) {
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      pinch.current = { startDistance: distance(a!, b!), startZoom: z };
      dragging.current = null;
      return;
    }
    if (!canPan) return;
    if ((e.target as HTMLElement).closest(".task-box, .spec-root-card, button")) return;
    dragging.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  }
  function onPointerMove(e: PointerEvent) {
    if (pointers.current.has(e.pointerId)) pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch.current && pointers.current.size >= 2) {
      const [a, b] = [...pointers.current.values()];
      if (pinch.current.startDistance > 0) setZoom(clampZoom(pinch.current.startZoom * (distance(a!, b!) / pinch.current.startDistance)));
      return;
    }
    if (!dragging.current) return;
    setPanClamped({ x: e.clientX - dragging.current.x, y: e.clientY - dragging.current.y });
  }
  function onPointerUp(e: PointerEvent) {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size < 2 && pinch.current) {
      pinch.current = null;
      // Pinch-zooming out below fit left a stale pan offset, same as the
      // "−" button; reset once the gesture ends rather than mid-pinch.
      if (z <= fit + 0.001) setPan({ x: 0, y: 0 });
    }
    dragging.current = null;
  }
  function onWheel(e: WheelEvent) {
    if (!e.ctrlKey) {
      if (!tallerThanView) return;
      const next = clampPan({ x: pan.x, y: pan.y - e.deltaY }, content, view);
      if (next.y !== pan.y) { e.preventDefault(); setPan(next); }
      return;
    }
    e.preventDefault();
    const next = clampZoom(z - e.deltaY * 0.001);
    setZoom(next);
    if (next <= fit + 0.001) setPan({ x: 0, y: 0 });
  }

  return (
    <div class="canvas-wrap">
      {hiddenDone > 0 ? <button type="button" class="canvas-done-toggle" onClick={() => setShowDone(true)}>{hiddenDone} done</button> : showDone && fullData.tasks.length > COLLAPSE_DONE_OVER ? <button type="button" class="canvas-done-toggle" onClick={() => setShowDone(false)}>Hide finished</button> : null}
      <div class="canvas-viewport" ref={viewportRef} style={{ height: Math.ceil(lay.height * z) + "px", touchAction: canPan ? "none" : "pan-y" }} onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp} onPointerLeave={onPointerUp} onWheel={onWheel}>
        <div class="canvas-frame" style={{ width: lay.width + "px", height: lay.height + "px", transform: `translate(${pan.x + centerX}px, ${pan.y}px) scale(${z})` }}>
          <svg class="dag canvas-arrows" width={lay.width} height={lay.height} aria-hidden="true">
            <defs><marker id="dag-arrowhead" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="dag-arrowhead" /></marker></defs>
            {lay.arrows.map((a) => (
              <g key={a.from + ">" + a.to}>
                <path class={"flow-arrow " + a.kind} d={a.path} marker-end="url(#dag-arrowhead)" />
                {a.label ? (
                  <g transform={`translate(${a.label.x},${a.label.y})`}>
                    {/* Only a truncated label needs the full text as a tooltip; an identical <title> would duplicate the visible text. */}
                    {a.label.text !== a.label.full ? <title>{a.label.full}</title> : null}
                    <rect class="arrow-label-bg" x={-a.label.w / 2} y={-11} width={a.label.w} height={22} rx={11} />
                    <text class="arrow-label" text-anchor="middle" dy="4">{a.label.text}</text>
                  </g>
                ) : null}
              </g>
            ))}
          </svg>
          {data.spec ? <SpecRoot spec={data.spec} style={boxStyle(lay.pos[data.spec.id]!)} /> : null}
          {data.tasks.map((t) => <TaskBox key={t.id} task={t} needsYou={needsYou} projectPhase={projectPhase} style={boxStyle(lay.pos[t.id]!)} />)}
          {lay.pos[PLACEHOLDER_ID] && data.spec ? <DagPlaceholder planning={planning} style={boxStyle(lay.pos[PLACEHOLDER_ID]!)} /> : null}
        </div>
      </div>
      <div class="canvas-zoom-controls">
        <button
          type="button"
          class="icon-btn"
          aria-label="zoom out"
          onClick={() => {
            const next = Math.max(MIN_ZOOM, z - 0.15);
            setZoom(next);
            // Zooming out below fit with a stale pan offset left the diagram
            // stuck off-center until "Fit" was pressed; reset pan whenever
            // the new zoom drops to (or below) fit, matching "Fit"'s own reset.
            if (next <= fit + 0.001) setPan({ x: 0, y: 0 });
          }}
        >
          −
        </button>
        <button type="button" class="icon-btn" aria-label="fit to width" onClick={() => { setZoom(null); setPan({ x: 0, y: 0 }); }}>Fit</button>
        <button type="button" class="icon-btn" aria-label="zoom in" onClick={() => setZoom(Math.min(MAX_ZOOM, z + 0.15))}>+</button>
      </div>
    </div>
  );
}
