import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { DEFAULT_COLORS, HIGHLIGHT_OPACITY, LABEL_FONT, labelSize, markColor, moveMark, resizeHighlight, resizeLabel } from "../marks";
import type { Handle } from "../marks";
import type { Mark, MarkColors } from "../types";
import type { Box } from "../pick";

interface Props {
  imageUrl: string;
  width: number;
  height: number;
  marks: Mark[];
  /** Default colours for marks without their own. */
  colors?: MarkColors;
  selectedId: string | null;
  editable: boolean;
  /** A field's position to point at (dashed blue), in image pixels. */
  focusBox: [number, number, number, number] | null;
  onSelect: (id: string | null) => void;
  onChange: (marks: Mark[]) => void;
  /** While set, a click or a dragged box on the page picks a value (instead of editing marks). */
  picking?: string | null;
  onPick?: (box: Box) => void;
}

type Drag =
  | { kind: "move"; id: string; start: DOMPoint; original: Mark }
  | { kind: "resize"; id: string; handle: Handle; start: DOMPoint; original: Mark };

const HANDLES: Handle[] = ["nw", "ne", "sw", "se"];

export function PageViewer({ imageUrl, width, height, marks, colors = DEFAULT_COLORS, selectedId, editable, focusBox, onSelect, onChange, picking = null, onPick }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1); // 1 = fit to panel width
  // The view is moved by dragging with the middle button, with Space held, or
  // with the hand tool on; a plain left drag is for marks and picking.
  const [pan, setPan] = useState<{ x: number; y: number; left: number; top: number } | null>(null);
  const [handTool, setHandTool] = useState(false);
  const [spaceHeld, setSpaceHeld] = useState(false);
  const hand = handTool || spaceHeld;
  // The point kept under the cursor while zooming with Ctrl + wheel.
  const zoomAnchor = useRef<{ x: number; y: number; px: number; py: number; ratio: number } | null>(null);
  const [drag, setDrag] = useState<Drag | null>(null);
  const [draft, setDraft] = useState<Mark[] | null>(null); // marks while dragging
  const [pickStart, setPickStart] = useState<DOMPoint | null>(null);
  const [pickBox, setPickBox] = useState<Box | null>(null);
  const shown = draft ?? marks;
  const page = { width, height };
  const handleSize = Math.max(10, width / 120);

  // Bring the focused field into view.
  useEffect(() => {
    if (!focusBox || !scrollRef.current || !svgRef.current) return;
    const scale = svgRef.current.getBoundingClientRect().width / width;
    const [x, y, w, h] = focusBox;
    const panel = scrollRef.current;
    panel.scrollTo({
      left: (x + w / 2) * scale - panel.clientWidth / 2,
      top: (y + h / 2) * scale - panel.clientHeight / 2,
      behavior: "smooth",
    });
  }, [focusBox, width]);

  // Ctrl + wheel zooms around the cursor (a native listener: React's is passive).
  useEffect(() => {
    const panel = scrollRef.current;
    if (!panel) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      const rect = panel.getBoundingClientRect();
      const x = event.clientX - rect.left;
      const y = event.clientY - rect.top;
      setZoom((current) => {
        const next = Math.min(4, Math.max(0.5, current * (event.deltaY < 0 ? 1.15 : 1 / 1.15)));
        zoomAnchor.current = { x, y, px: panel.scrollLeft + x, py: panel.scrollTop + y, ratio: next / current };
        return next;
      });
    };
    panel.addEventListener("wheel", onWheel, { passive: false });
    return () => panel.removeEventListener("wheel", onWheel);
  }, []);

  useLayoutEffect(() => {
    const anchor = zoomAnchor.current;
    const panel = scrollRef.current;
    if (!anchor || !panel) return;
    zoomAnchor.current = null;
    panel.scrollLeft = anchor.px * anchor.ratio - anchor.x;
    panel.scrollTop = anchor.py * anchor.ratio - anchor.y;
  }, [zoom]);

  // Space works like the hand tool while held (not while typing in a field).
  useEffect(() => {
    const typing = (event: KeyboardEvent) => ["INPUT", "TEXTAREA", "SELECT"].includes((event.target as HTMLElement).tagName);
    const down = (event: KeyboardEvent) => {
      if (event.code === "Space" && !typing(event)) {
        event.preventDefault(); // no page scroll
        setSpaceHeld(true);
      }
    };
    const up = (event: KeyboardEvent) => {
      if (event.code === "Space") setSpaceHeld(false);
    };
    const reset = () => setSpaceHeld(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", reset);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", reset);
    };
  }, []);

  const panStart = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!scrollRef.current) return;
    if (event.button === 0 && !hand) {
      onSelect(null); // a plain click on the page clears the selection
      return;
    }
    if (event.button !== 1 && !(event.button === 0 && hand)) return;
    event.preventDefault();
    const panel = scrollRef.current;
    try {
      panel.setPointerCapture(event.pointerId);
    } catch {
      // not an active pointer
    }
    setPan({ x: event.clientX, y: event.clientY, left: panel.scrollLeft, top: panel.scrollTop });
  };
  const panMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!pan || !scrollRef.current) return;
    scrollRef.current.scrollLeft = pan.left - (event.clientX - pan.x);
    scrollRef.current.scrollTop = pan.top - (event.clientY - pan.y);
  };
  const panEnd = () => setPan(null);

  const toImage = (event: ReactPointerEvent): DOMPoint => {
    const svg = svgRef.current!;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM()!.inverse());
  };

  const begin = (event: ReactPointerEvent, mark: Mark, handle?: Handle) => {
    if (event.button !== 0 || hand) return; // moving the view: let the page handle it
    event.stopPropagation();
    onSelect(mark.id);
    if (!editable) return;
    try {
      (event.target as Element).setPointerCapture(event.pointerId);
    } catch {
      // not an active pointer
    }
    const start = toImage(event);
    setDrag(handle ? { kind: "resize", id: mark.id, handle, start, original: mark } : { kind: "move", id: mark.id, start, original: mark });
  };

  const update = (event: ReactPointerEvent) => {
    if (!drag) return;
    const point = toImage(event);
    const dx = point.x - drag.start.x;
    const dy = point.y - drag.start.y;
    let changed: Mark;
    if (drag.kind === "move") changed = moveMark(drag.original, dx, dy, page);
    else if (drag.original.type === "highlight") changed = resizeHighlight(drag.original, drag.handle, dx, dy, page);
    else changed = resizeLabel(drag.original, dy);
    setDraft(marks.map((mark) => (mark.id === drag.id ? changed : mark)));
  };

  const end = () => {
    if (drag && draft) onChange(draft);
    setDrag(null);
    setDraft(null);
  };

  const selected = shown.find((mark) => mark.id === selectedId) ?? null;

  const boxFrom = (start: DOMPoint, point: DOMPoint): Box => ({
    x: Math.min(start.x, point.x),
    y: Math.min(start.y, point.y),
    w: Math.abs(point.x - start.x),
    h: Math.abs(point.y - start.y),
  });
  const pickDown = (event: ReactPointerEvent) => {
    if (event.button !== 0 || hand) return; // moving the view: let the page handle it
    event.stopPropagation();
    try {
      (event.target as Element).setPointerCapture(event.pointerId);
    } catch {
      // not an active pointer (e.g. a synthetic event); the drag still works inside the page
    }
    const start = toImage(event);
    setPickStart(start);
    setPickBox({ x: start.x, y: start.y, w: 0, h: 0 });
  };
  const pickMove = (event: ReactPointerEvent) => {
    if (pickStart) setPickBox(boxFrom(pickStart, toImage(event)));
  };
  const pickUp = (event: ReactPointerEvent) => {
    if (!pickStart) return;
    const box = boxFrom(pickStart, toImage(event));
    setPickStart(null);
    setPickBox(null);
    onPick?.(box);
  };

  return (
    <div className="viewer">
      <div className="viewer-toolbar">
        <button onClick={() => setZoom((value) => Math.max(0.5, value / 1.25))} aria-label="Zoom out">−</button>
        <span className="zoom-value">{Math.round(zoom * 100)}%</span>
        <button onClick={() => setZoom((value) => Math.min(4, value * 1.25))} aria-label="Zoom in">+</button>
        <button onClick={() => setZoom(1)}>Fit width</button>
        <button
          className={handTool ? "hand active" : "hand"}
          aria-pressed={handTool}
          onClick={() => setHandTool((value) => !value)}
          title="Hand tool: drag to move the view (or hold Space, or drag with the middle button)"
        >
          ✋ Move view
        </button>
        <span className={picking ? "hint picking" : "hint"}>
          {picking ??
            (hand
              ? "Drag to move the view"
              : editable
                ? "Drag marks to move · Space + drag or middle button to move the view · Ctrl + wheel to zoom"
                : "Submitted — reopen to edit · Space + drag to move the view · Ctrl + wheel to zoom")}
        </span>
      </div>
      <div
        className={["viewer-scroll", hand ? "hand" : "", pan ? "panning" : ""].join(" ")}
        ref={scrollRef}
        onMouseDown={(event) => event.button === 1 && event.preventDefault()} // no browser autoscroll
        onPointerDown={panStart}
        onPointerMove={panMove}
        onPointerUp={panEnd}
        onPointerCancel={() => setPan(null)}
      >
        <div className="viewer-page" style={{ width: `${zoom * 100}%` }}>
          <img src={imageUrl} alt="Bill page" draggable={false} />
          <svg
            ref={svgRef}
            viewBox={`0 0 ${width} ${height}`}
            className={editable ? "marks editable" : "marks"}
            onPointerMove={update}
            onPointerUp={end}
            onPointerCancel={end}
          >
            {focusBox && (
              <rect
                className="focus-box"
                x={focusBox[0] - 6}
                y={focusBox[1] - 6}
                width={focusBox[2] + 12}
                height={focusBox[3] + 12}
                strokeWidth={width / 400}
              />
            )}
            {shown.map((mark) =>
              mark.type === "highlight" ? (
                <rect
                  key={mark.id}
                  className="highlight"
                  fill={markColor(mark, colors)}
                  fillOpacity={HIGHLIGHT_OPACITY}
                  x={mark.x}
                  y={mark.y}
                  width={mark.w}
                  height={mark.h}
                  onPointerDown={(event) => begin(event, mark)}
                />
              ) : (
                <text
                  key={mark.id}
                  className="pen"
                  fill={markColor(mark, colors)}
                  x={mark.x}
                  y={mark.y + mark.fontSize * 0.85}
                  fontSize={mark.fontSize}
                  fontFamily={LABEL_FONT}
                  fontWeight={700}
                  onPointerDown={(event) => begin(event, mark)}
                >
                  {mark.text}
                </text>
              ),
            )}
            {selected && !picking && <Selection mark={selected} handleSize={handleSize} editable={editable} onHandle={begin} />}
            {picking && (
              <rect
                className="pick-layer"
                x={0}
                y={0}
                width={width}
                height={height}
                onPointerDown={pickDown}
                onPointerMove={pickMove}
                onPointerUp={pickUp}
              />
            )}
            {pickBox && pickBox.w + pickBox.h > 0 && (
              <rect className="pick-box" x={pickBox.x} y={pickBox.y} width={pickBox.w} height={pickBox.h} strokeWidth={width / 500} />
            )}
          </svg>
        </div>
      </div>
    </div>
  );
}

function Selection({
  mark,
  handleSize,
  editable,
  onHandle,
}: {
  mark: Mark;
  handleSize: number;
  editable: boolean;
  onHandle: (event: ReactPointerEvent, mark: Mark, handle: Handle) => void;
}) {
  const box = mark.type === "highlight" ? { x: mark.x, y: mark.y, w: mark.w, h: mark.h } : { x: mark.x, y: mark.y, ...labelSize(mark) };
  const corners: Record<Handle, [number, number]> = {
    nw: [box.x, box.y],
    ne: [box.x + box.w, box.y],
    sw: [box.x, box.y + box.h],
    se: [box.x + box.w, box.y + box.h],
  };
  // Labels scale from one corner; highlights from all four.
  const handles = mark.type === "highlight" ? HANDLES : (["se"] as Handle[]);
  return (
    <g>
      <rect className="selection" x={box.x} y={box.y} width={box.w} height={box.h} strokeWidth={handleSize / 4} />
      {editable &&
        handles.map((handle) => (
          <rect
            key={handle}
            className={`handle handle-${handle}`}
            x={corners[handle][0] - handleSize / 2}
            y={corners[handle][1] - handleSize / 2}
            width={handleSize}
            height={handleSize}
            onPointerDown={(event) => onHandle(event, mark, handle)}
          />
        ))}
    </g>
  );
}
