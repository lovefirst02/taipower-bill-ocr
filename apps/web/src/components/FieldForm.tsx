import type { Column, Issue, Row } from "../types";

interface Props {
  columns: Column[];
  row: Row;
  ocrRow: Row;
  issues: Issue[];
  alternatives: Record<string, string | number>;
  editable: boolean;
  focused: string | null;
  onFocus: (column: string) => void;
  onChange: (column: string, value: string | number | null) => void;
}

const SECTIONS: { title: string; match: (key: string) => boolean }[] = [
  { title: "Bill", match: (key) => key.startsWith("Billing Period") || key.startsWith("Custom No.") },
  { title: "Billed kWh 計費度數", match: (key) => key.startsWith("計費度數_") },
  { title: "Transferred kWh 轉供度數", match: (key) => key.startsWith("轉供度數_") },
  {
    title: "Charges (TWD) 計費內容",
    match: (key) => ["基本電費", "流動電費", "功率因數調整費", "預繳費用", "其他調整費用", "稅前應繳總金額", "應繳總金額"].includes(key),
  },
];

const format = (value: string | number | null | undefined) => (value === null || value === undefined ? "" : String(value));

function parse(column: Column, text: string): string | number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (!column.numeric) return trimmed;
  const number = Number(trimmed.replace(/,/g, ""));
  return Number.isFinite(number) ? number : trimmed;
}

export function FieldForm({ columns, row, ocrRow, issues, alternatives, editable, focused, onFocus, onChange }: Props) {
  const flagged = new Set(issues.flatMap((issue) => issue.columns));
  return (
    <div className="field-form">
      {SECTIONS.map((section) => (
        <fieldset key={section.title}>
          <legend>{section.title}</legend>
          {columns
            .filter((column) => section.match(column.key))
            .map((column) => {
              const value = row[column.key];
              const edited = format(value) !== format(ocrRow[column.key]);
              const alternative = alternatives[column.key];
              const invalid = column.numeric && typeof value === "string" && value !== "";
              const classes = [
                "field",
                flagged.has(column.key) && !edited ? "flagged" : "",
                edited ? "edited" : "",
                invalid ? "invalid" : "",
                focused === column.key ? "focused" : "",
              ].join(" ");
              return (
                <label key={column.key} className={classes}>
                  <span className="field-label">
                    {column.label}
                    <small>{column.key.replace(/^.*_/, "").replace(/^(Billing Period|Custom No\.) /, "")}</small>
                  </span>
                  <input
                    value={format(value)}
                    readOnly={!editable || !column.editable}
                    inputMode={column.numeric ? "decimal" : "text"}
                    onFocus={() => onFocus(column.key)}
                    onChange={(event) => onChange(column.key, parse(column, event.target.value))}
                  />
                  {editable && alternative !== undefined && format(alternative) !== format(value) && (
                    <button type="button" className="alt" onClick={() => onChange(column.key, alternative)}>
                      Use {format(alternative)}
                    </button>
                  )}
                </label>
              );
            })}
        </fieldset>
      ))}
    </div>
  );
}
