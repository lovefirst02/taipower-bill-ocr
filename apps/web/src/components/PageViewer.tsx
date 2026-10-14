import { useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { DEFAULT_COLORS, HIGHLIGHT_OPACITY, LABEL_FONT, labelSize, markColor, moveMark, resizeHighlight, resizeLabel } from "../marks";
import type { Handle } from "../marks";
import type { Mark, MarkColors } from "../types";

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
}

type Drag =
  | { kind: "move"; id: string; start: DOMPoint; original: Mark }
  | { kind: "resize"; id: string; handle: Handle; start: DOMPoint; original: Mark };

const HANDLES: Handle[] = ["nw", "ne", "sw", "se"];

export function PageViewer({ imageUrl, width, height, marks, colors = DEFAULT_COLORS, selectedId, editable, focusBox, onSelect, onChange }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [zoom, setZoom] = useState(1); // 1 = fit to panel width
  const [drag, setDrag] = useState<Drag | null>(null);
  const [draft, setDraft] = useState<Mark[] | null>(null); // marks while dragging
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

  const toImage = (event: ReactPointerEvent): DOMPoint => {
    const svg = svgRef.current!;
    const point = svg.createSVGPoint();
    point.x = event.clientX;
    point.y = event.clientY;
    return point.matrixTransform(svg.getScreenCTM()!.inverse());
  };

  const begin = (event: ReactPointerEvent, mark: Mark, handle?: Handle) => {
    event.stopPropagation();
    onSelect(mark.id);
    if (!editable) return;
    (event.target as Element).setPointerCapture(event.pointerId);
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

  return (
    <div className="viewer">
      <div className="viewer-toolbar">
        <button onClick={() => setZoom((value) => Math.max(0.5, value / 1.25))} aria-label="Zoom out">−</button>
        <span className="zoom-value">{Math.round(zoom * 100)}%</span>
        <button onClick={() => setZoom((value) => Math.min(4, value * 1.25))} aria-label="Zoom in">+</button>
        <button onClick={() => setZoom(1)}>Fit width</button>
        <span className="hint">{editable ? "Drag marks to move · corners to resize" : "Submitted — reopen to edit"}</span>
      </div>
      <div className="viewer-scroll" ref={scrollRef} onPointerDown={() => onSelect(null)}>
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
            {selected && <Selection mark={selected} handleSize={handleSize} editable={editable} onHandle={begin} />}
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
