import { useRef, useState } from "preact/hooks";
import type { CanvasData } from "./canvasData";
import { computeLayout, PAD, GAP_Y, BOX_W, BOX_H_BASE } from "./flowLayout";
import { TaskBox } from "./TaskBox";
import { SpecRoot } from "./SpecRoot";
import { GhostBox } from "./GhostBox";
import { pendingCreates, pendingBreakdowns } from "./pending";
import { Icon } from "../ui/Icon";

export function Canvas({ data, projectId, projectPhase, needsYou }: { data: CanvasData; projectId: number; projectPhase: string; needsYou: Set<number> }) {
  const lay = computeLayout(data.tasks, data.edges);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const dragging = useRef<{ x: number; y: number } | null>(null);

  function onPointerDown(e: PointerEvent) {
    if ((e.target as HTMLElement).closest(".task-box, .spec-root-card, button")) return;
    dragging.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }
  function onPointerMove(e: PointerEvent) {
    if (!dragging.current) return;
    setPan({ x: e.clientX - dragging.current.x, y: e.clientY - dragging.current.y });
  }
  function onPointerUp() { dragging.current = null; }
  function onWheel(e: WheelEvent) {
    if (!e.ctrlKey) return;
    e.preventDefault();
    setZoom((z) => Math.min(2, Math.max(0.4, z - e.deltaY * 0.001)));
  }
  const fit = () => { setZoom(1); setPan({ x: 0, y: 0 }); };

  const pendingCreateGhosts = Object.entries(pendingCreates.value).filter(([, p]) => p.kind === "task");
  const breakdownGhosts = Object.entries(pendingBreakdowns.value).filter(([nodeId]) => Number(nodeId) === data.spec?.id);
  // Ghosts render below the real, laid-out boxes — same x/y placement pattern
  // Canvas.tsx uses for real TaskBoxes (flowLayout's PAD/GAP_Y/BOX_H_BASE),
  // stacked one per row so they never sit on top of a real box at (0,0).
  const ghostY = (i: number) => PAD + lay.height + i * (BOX_H_BASE + GAP_Y);

  return (
    <div class="canvas-wrap">
      <div class="canvas-zoom-controls">
        <button type="button" class="icon-btn" aria-label="zoom out" onClick={() => setZoom((z) => Math.max(0.4, z - 0.1))}>−</button>
        <button type="button" class="icon-btn" aria-label="fit" onClick={fit}><Icon name="activity" /></button>
        <button type="button" class="icon-btn" aria-label="zoom in" onClick={() => setZoom((z) => Math.min(2, z + 0.1))}>+</button>
      </div>
      <div class="canvas-viewport" onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onWheel={onWheel}>
        <div class="canvas-frame" style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`, width: lay.width + "px", height: lay.height + 96 + "px" }}>
          <div class="canvas-spec-slot"><SpecRoot spec={data.spec} projectId={projectId} taskCount={data.tasks.length} projectPhase={projectPhase} /></div>
          <svg class="canvas-arrows" width={lay.width} height={lay.height} style={{ marginTop: "96px" }}>
            {lay.arrows.map((a) => (
              <>
                <path class="flow-arrow" d={a.path} />
                {a.label ? <g transform={`translate(${a.label.x},${a.label.y})`}><rect class="arrow-label-bg" x={-40} y={-10} width={80} height={20} rx={10} /><text class="arrow-label" textAnchor="middle" dy="4">{a.label.text}</text></g> : null}
              </>
            ))}
          </svg>
          <div class="canvas-boxes" style={{ marginTop: "96px" }}>
            {data.tasks.map((t) => <TaskBox task={t} needsYou={needsYou} projectPhase={projectPhase} style={{ position: "absolute", left: lay.pos[t.id]!.x + "px", top: lay.pos[t.id]!.y + "px", width: lay.pos[t.id]!.w + "px" }} />)}
            {pendingCreateGhosts.map(([key, p], i) => <GhostBox key={key} title={p.title} caption="creating…" error={p.error} style={{ position: "absolute", left: PAD + "px", top: ghostY(i) + "px", width: BOX_W + "px" }} />)}
            {breakdownGhosts.flatMap(([, p], gi) => [0, 1, 2].map((i) => <GhostBox key={p.nodeId + ":" + i} title="…" caption={`${p.agent} is breaking this down…`} logLink={p.log} style={{ position: "absolute", left: PAD + "px", top: ghostY(pendingCreateGhosts.length + gi * 3 + i) + "px", width: BOX_W + "px" }} />))}
          </div>
        </div>
      </div>
    </div>
  );
}
