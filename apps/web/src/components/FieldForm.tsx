import { useState } from "react";
import type { Column, Issue, Row, ValueSource } from "../types";

interface Props {
  columns: Column[];
  row: Row;
  ocrRow: Row;
  issues: Issue[];
  alternatives: Record<string, string | number>;
  editable: boolean;
  focused: string | null;
  onFocus: (column: string) => void;
  onChange: (column: string, value: string | number | null, source: ValueSource) => void;
  /** Where each value came from; shown beside the field. */
  sources?: Record<string, ValueSource>;
  /** The field whose value is being picked on the image. */
  picking?: string | null;
  onPick?: (column: string | null) => void;
}

const SECTIONS: { title: string; match: (key: string) => boolean }[] = [
  { title: "Bill", match: (key) => key.startsWith("Billing Period") || key.startsWith("Custom No.") },
  { title: "Usage kWh 用電度數", match: (key) => key === "用電度數" },
  { title: "Billed kWh 計費度數", match: (key) => key.startsWith("計費度數_") },
  { title: "Transferred kWh 轉供度數", match: (key) => key.startsWith("轉供度數_") },
  {
    title: "Charges (TWD) 計費內容",
    match: (key) => ["基本電費", "流動電費", "功率因數調整費", "預繳費用", "其他調整費用", "稅前應繳總金額", "應繳總金額"].includes(key),
  },
];

const SOURCE_LABELS: Record<ValueSource, string> = {
  taipower: "Taipower bill",
  keyword: "Found by keyword",
  pick: "Picked on image",
  manual: "Typed",
  alternative: "Other reading",
};

const format = (value: string | number | null | undefined) => (value === null || value === undefined ? "" : String(value));

export function parse(column: Column, text: string): string | number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (!column.numeric) return trimmed;
  const number = Number(trimmed.replace(/,/g, ""));
  return Number.isFinite(number) ? number : trimmed;
}

type Value = string | number | null | undefined;

/**
 * Shows what is typed while the field is being edited ("12." or "1.50"),
 * while the parsed number is what is saved; turning "12." into 12 on every
 * keystroke would drop the decimal point before its digits could be typed.
 */
function FieldInput({ column, value, readOnly, onFocus, onChange }: {
  column: Column;
  value: Value;
  readOnly: boolean;
  onFocus: () => void;
  onChange: (value: string | number | null) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  // A value changed elsewhere (e.g. "Use …") replaces the text being typed.
  const shown = draft !== null && format(parse(column, draft)) === format(value) ? draft : format(value);
  return (
    <input
      value={shown}
      readOnly={readOnly}
      inputMode={column.numeric ? "decimal" : "text"}
      onFocus={onFocus}
      onBlur={() => setDraft(null)}
      onChange={(event) => {
        setDraft(event.target.value);
        onChange(parse(column, event.target.value));
      }}
    />
  );
}

export function FieldForm({
  columns, row, ocrRow, issues, alternatives, editable, focused, onFocus, onChange,
  sources = {}, picking = null, onPick,
}: Props) {
  const flagged = new Set(issues.flatMap((issue) => issue.columns));
  return (
    <div className="field-form">
      {SECTIONS.map((section) => {
        const inSection = columns.filter((column) => section.match(column.key));
        if (!inSection.length) return null;
        return (
          <fieldset key={section.title}>
            <legend>{section.title}</legend>
            {inSection.map((column) => {
              const value = row[column.key];
              const edited = format(value) !== format(ocrRow[column.key]);
              const alternative = alternatives[column.key];
              const invalid = column.numeric && typeof value === "string" && value !== "";
              const source = sources[column.key];
              const canEdit = editable && column.editable;
              const classes = [
                "field",
                flagged.has(column.key) && !edited ? "flagged" : "",
                edited ? "edited" : "",
                invalid ? "invalid" : "",
                focused === column.key ? "focused" : "",
              ].join(" ");
              const showAlternative = editable && alternative !== undefined && format(alternative) !== format(value);
              return (
                <label key={column.key} className={classes}>
                  <span className="field-label">
                    {column.label}
                    <small>
                      {column.key.replace(/^.*_/, "").replace(/^(Billing Period|Custom No\.) /, "")}
                      {source && <span className="source">· {SOURCE_LABELS[source] ?? source}</span>}
                    </small>
                  </span>
                  <FieldInput
                    column={column}
                    value={value}
                    readOnly={!canEdit}
                    onFocus={() => onFocus(column.key)}
                    onChange={(next) => onChange(column.key, next, "manual")}
                  />
                  {(canEdit && onPick) || showAlternative ? (
                    <span className="field-actions">
                      {canEdit && onPick && (
                        <button
                          type="button"
                          className={picking === column.key ? "pick active" : "pick"}
                          title="Click the value on the page (or drag a box around it) to fill this field and mark it"
                          onClick={(event) => { event.preventDefault(); onPick(picking === column.key ? null : column.key); }}
                        >
                          {picking === column.key ? "Picking… (Esc to stop)" : "Pick on image"}
                        </button>
                      )}
                      {showAlternative && (
                        <button type="button" className="alt" onClick={() => onChange(column.key, alternative, "alternative")}>
                          Use {format(alternative)}
                        </button>
                      )}
                    </span>
                  ) : null}
                </label>
              );
            })}
          </fieldset>
        );
      })}
    </div>
  );
}
