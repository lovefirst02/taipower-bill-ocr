import { useState } from "react";
import { DEFAULT_COLORS, HIGHLIGHT_OPACITY, markColor } from "../marks";
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
  /** Clears notes the reviewer has dealt with (all when no note is given). */
  onDismissNote?: (note?: string) => void;
  /** The user's tags: each makes a highlight and its red label, picked on the image. */
  tags?: string[];
  defaultTags?: string[];
  /** The tag being placed on the image. */
  tagging?: string | null;
  onTag?: (tag: string | null) => void;
  onSaveTags?: (tags: string[] | null) => Promise<void>;
}

export function MarksPanel({
  marks, colors = DEFAULT_COLORS, notes, selectedId, editable, onSelect, onChange, onAddHighlight, onAddLabel, onReset,
  onDismissNote, tags = [], defaultTags = [], tagging = null, onTag, onSaveTags,
}: Props) {
  const [editingTags, setEditingTags] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const update = (id: string, patch: Partial<Mark>) =>
    onChange(marks.map((mark) => (mark.id === id ? ({ ...mark, ...patch } as Mark) : mark)));
  const remove = (id: string) => onChange(marks.filter((mark) => mark.id !== id));

  const saveTags = async (value: string[] | null) => {
    if (!onSaveTags) return;
    setSaving(true);
    try {
      await onSaveTags(value);
      setEditingTags(null);
    } finally {
      setSaving(false);
    }
  };

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
      {notes.length > 0 && (
        <div className="notes">
          {notes.map((note) => (
            <p key={note} className="note">
              <span>⚠ {note}</span>
              {editable && onDismissNote && (
                <button type="button" className="icon" aria-label="Clear this note" title="Clear this note" onClick={() => onDismissNote(note)}>✕</button>
              )}
            </p>
          ))}
          {editable && onDismissNote && notes.length > 1 && (
            <button type="button" className="link clear-all" onClick={() => onDismissNote()}>Clear all notes</button>
          )}
        </div>
      )}
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
        <>
          <div className="tags">
            <span className="tags-title" title="Click a tag, then click the value on the page (or drag a box around it): a highlight and its label are added together">
              Tags
            </span>
            {editingTags === null ? (
              <>
                {tags.map((tag) => (
                  <button
                    key={tag}
                    type="button"
                    className={tagging === tag ? "tag active" : "tag"}
                    title={`Click the value on the page to highlight it and write “${tag}” beside it`}
                    onClick={() => onTag?.(tagging === tag ? null : tag)}
                  >
                    {tag}
                  </button>
                ))}
                {onSaveTags && (
                  <button type="button" className="link" onClick={() => setEditingTags(tags.join("\n"))}>Edit tags</button>
                )}
              </>
            ) : (
              <div className="tag-editor">
                <textarea
                  rows={Math.max(3, tags.length + 1)}
                  value={editingTags}
                  autoFocus
                  placeholder="One tag per line"
                  onChange={(event) => setEditingTags(event.target.value)}
                />
                <div className="tag-editor-actions">
                  <button type="button" className="link" disabled={saving} onClick={() => saveTags(null)} title={defaultTags.join(", ")}>
                    Default tags
                  </button>
                  <button type="button" disabled={saving} onClick={() => setEditingTags(null)}>Cancel</button>
                  <button type="button" className="primary" disabled={saving} onClick={() => saveTags(editingTags.split("\n"))}>Save tags</button>
                </div>
              </div>
            )}
          </div>
          <div className="add-marks">
            <button type="button" onClick={onAddHighlight}>+ Highlight</button>
            <button type="button" onClick={() => onAddLabel("Note")}>+ Text</button>
          </div>
        </>
      )}
    </div>
  );
}
