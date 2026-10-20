import { cellNumber, checkLine, isGreen } from "../green";
import type { Cell } from "../green";
import type { GreenView } from "../types";

/** A statement as edited: cells stay as typed until saved. */
export interface GreenDraft {
  period: string;
  lines: Record<string, Cell>[];
}

const COLUMNS: { key: string; label: string; numeric: boolean; width?: number }[] = [
  { key: "meter", label: "電號", numeric: false, width: 96 },
  { key: "energy", label: "能源別", numeric: false, width: 64 },
  { key: "base", label: "基準價", numeric: true },
  { key: "transfer_rate", label: "轉供費率", numeric: true },
  { key: "certificate_rate", label: "憑證費率", numeric: true },
  { key: "kwh", label: "轉供度數", numeric: true },
  { key: "cost", label: "本月電費", numeric: true },
];

const HOW: Record<string, string> = {
  "same period": "same period",
  prorated: "periods differ — Taipower kWh prorated by days",
  none: "no Taipower bill with this 電號",
  "no period": "enter the statement period to compare",
};

const format = (value: number | null) => (value === null ? "—" : value.toLocaleString());

interface Props {
  view: GreenView;
  draft: GreenDraft;
  editable: boolean;
  onChange: (draft: GreenDraft) => void;
}

/** A retailer's green power statement: its lines, each checked, and the Taipower cross-check. */
export function GreenTable({ view, draft, editable, onChange }: Props) {
  const setCell = (index: number, key: string, value: string) =>
    onChange({ ...draft, lines: draft.lines.map((line, at) => (at === index ? { ...line, [key]: value } : line)) });
  const checks = draft.lines.map((line) => checkLine(line, view.tolerance));
  const totalKwh = draft.lines.reduce<number>((sum, line) => sum + (isGreen(line) ? cellNumber(line.kwh) ?? 0 : 0), 0);
  const totalCost = draft.lines.reduce<number>((sum, line) => sum + (cellNumber(line.cost) ?? 0), 0);
  const totalExpected = checks.reduce<number>((sum, check) => sum + (check.expected ?? 0), 0);

  return (
    <div className="green-table">
      <fieldset>
        <legend>Green power statement 綠電明細</legend>
        <label className="green-period">
          <span>Period 計費期間</span>
          <input
            value={draft.period}
            readOnly={!editable}
            placeholder="113/01/01~113/01/31"
            onChange={(event) => onChange({ ...draft, period: event.target.value })}
          />
        </label>
        <div className="green-scroll">
          <table>
            <thead>
              <tr>
                {COLUMNS.map((column) => <th key={column.key}>{column.label}</th>)}
                <th title="kWh × (基準價 + 轉供費率 + 憑證費率), rounded">計算金額</th>
                <th title={`Allowed: ±${view.tolerance} NT$ (Settings)`}>差額</th>
                {editable && <th aria-label="Remove" />}
              </tr>
            </thead>
            <tbody>
              {draft.lines.map((line, index) => {
                const check = checks[index];
                return (
                  <tr key={index} className={check.status}>
                    {COLUMNS.map((column) => (
                      <td key={column.key}>
                        <input
                          className={column.numeric ? "num" : ""}
                          style={column.width ? { width: column.width } : undefined}
                          value={line[column.key] ?? ""}
                          readOnly={!editable}
                          inputMode={column.numeric ? "decimal" : "text"}
                          onChange={(event) => setCell(index, column.key, event.target.value)}
                        />
                      </td>
                    ))}
                    <td className="num">{format(check.expected)}</td>
                    <td className={`num diff ${check.status}`} title={check.status === "check" ? "Outside the allowed difference: check the line" : undefined}>
                      {check.status === "incomplete" ? "missing" : `${check.status === "ok" ? "✓" : "⚠"} ${check.difference}`}
                    </td>
                    {editable && (
                      <td>
                        <button type="button" className="icon" aria-label={`Remove line ${index + 1}`}
                          onClick={() => onChange({ ...draft, lines: draft.lines.filter((_, at) => at !== index) })}>✕</button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr>
                <td colSpan={5}>Total (green kWh exclude surcharges such as 餘電加收)</td>
                <td className="num">{format(totalKwh)}</td>
                <td className="num">{format(totalCost)}</td>
                <td className="num">{format(totalExpected)}</td>
                <td className="num">{format(Math.round((totalCost - totalExpected) * 100) / 100)}</td>
                {editable && <td />}
              </tr>
            </tfoot>
          </table>
        </div>
        {editable && (
          <button type="button" className="link" onClick={() => onChange({ ...draft, lines: [...draft.lines, { meter: "", energy: "", kwh: "", cost: "" }] })}>
            + Line
          </button>
        )}
        <p className="hint">
          The statement&apos;s 本月電費 are what is reported. Retailers work them out from finer data, each rounded, so a few NT$ from
          kWh × rates is normal; the printed 每度單價 is only 本月電費 ÷ kWh.
        </p>
      </fieldset>

      <fieldset>
        <legend>Transferred kWh vs Taipower bills (as saved)</legend>
        {view.crosscheck.length === 0 ? (
          <p className="hint">No green power lines with a 電號.</p>
        ) : (
          <table className="crosscheck">
            <thead>
              <tr><th>電號</th><th>Site</th><th>Statement</th><th>Taipower</th><th>Difference</th></tr>
            </thead>
            <tbody>
              {view.crosscheck.map((entry) => (
                <tr key={entry.meter} className={entry.difference === 0 ? "ok" : entry.difference === null ? "" : "check"}>
                  <td>{entry.meter}</td>
                  <td>{entry.site ?? "—"}</td>
                  <td className="num">{format(entry.statement_kwh)}</td>
                  <td className="num">{format(entry.taipower_kwh)}</td>
                  <td>
                    {entry.difference === null ? HOW[entry.how] ?? entry.how : `${entry.difference === 0 ? "✓ 0" : `⚠ ${entry.difference}`} kWh`}
                    {entry.how === "prorated" && <span className="hint"> ({HOW.prorated})</span>}
                    {entry.bills.length > 0 && <span className="hint"> · {entry.bills.map((bill) => `${bill.filename} ${bill.period ?? ""}`).join(", ")}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </fieldset>
    </div>
  );
}
