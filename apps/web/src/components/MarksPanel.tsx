import { DEFAULT_COLORS, EVIDENCE_LABELS, HIGHLIGHT_OPACITY, markColor } from "../marks";
import type { Mark, MarkColors } from "../types";

interface Props {
  marks: Mark[];
  /** Default colours for marks without their own. */
  colors?: MarkColors;
  notes: string[];
  selectedId: string | null;
  editable: boolean;
  onSelect: (id: string) => void;
  onChange: (marks: Mark[]) => void;
  onAddHighlight: () => void;
  onAddLabel: (text: string) => void;
  onReset: () => void;
}

export function MarksPanel({ marks, colors = DEFAULT_COLORS, notes, selectedId, editable, onSelect, onChange, onAddHighlight, onAddLabel, onReset }: Props) {
  const update = (id: string, patch: Partial<Mark>) =>
    onChange(marks.map((mark) => (mark.id === id ? ({ ...mark, ...patch } as Mark) : mark)));
  const remove = (id: string) => onChange(marks.filter((mark) => mark.id !== id));

  return (
    <div className="marks-panel">
      <div className="panel-title">
        <h3>Evidence marks</h3>
        {editable && (
          <button type="button" className="link" onClick={onReset} title="Put the marks back where OCR found the values">
            Reset
          </button>
        )}
      </div>
      {notes.map((note) => (
        <p key={note} className="note">⚠ {note}</p>
      ))}
      <ul>
        {marks.map((mark) => (
          <li key={mark.id} className={mark.id === selectedId ? "selected" : ""} onClick={() => onSelect(mark.id)}>
            {mark.type === "highlight" ? (
              <span
                className="swatch highlight-swatch"
                style={{ background: markColor(mark, colors), opacity: Math.min(1, HIGHLIGHT_OPACITY * 2) }}
                aria-hidden
              />
            ) : (
              <span className="swatch pen-swatch" style={{ color: markColor(mark, colors) }} aria-hidden>A</span>
            )}
            {mark.type === "label" ? (
              <>
                <input
                  className="label-text"
                  style={{ color: markColor(mark, colors) }}
                  value={mark.text}
                  readOnly={!editable}
                  onChange={(event) => update(mark.id, { text: event.target.value })}
                />
                <input
                  className="label-size"
                  type="number"
                  min={12}
                  max={200}
                  value={Math.round(mark.fontSize)}
                  readOnly={!editable}
                  title="Font size (px)"
                  onChange={(event) => update(mark.id, { fontSize: Number(event.target.value) || 12 })}
                />
              </>
            ) : (
              <span className="mark-desc">Highlight {Math.round(mark.w)}×{Math.round(mark.h)}</span>
            )}
            <input
              type="color"
              className="mark-color"
              value={markColor(mark, colors)}
              disabled={!editable}
              title={mark.color ? "Colour of this mark" : "Default colour (change it here for this mark only)"}
              aria-label="Mark colour"
              onClick={(event) => event.stopPropagation()}
              onChange={(event) => update(mark.id, { color: event.target.value })}
            />
            {editable && (
              <button type="button" className="icon" aria-label="Delete mark" onClick={(event) => { event.stopPropagation(); remove(mark.id); }}>
                ✕
              </button>
            )}
          </li>
        ))}
      </ul>
      {editable && (
        <div className="add-marks">
          <button type="button" onClick={onAddHighlight}>+ Highlight</button>
          {EVIDENCE_LABELS.map((text) => (
            <button key={text} type="button" onClick={() => onAddLabel(text)}>+ “{text}”</button>
          ))}
          <button type="button" onClick={() => onAddLabel("Note")}>+ Text</button>
        </div>
      )}
    </div>
  );
}
