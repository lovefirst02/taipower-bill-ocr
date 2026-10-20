import { useState } from "react";
import { cellNumber, checkLine, checkNotice, checkRow, isGreen, meterNumber } from "../green";
import type { Cell } from "../green";
import type { GreenView, MeterComparison, ServiceComparison } from "../types";

/** A green power document as edited: cells stay as typed until saved. */
export interface GreenDraft {
  period: string;
  service?: string;
  lines?: Record<string, Cell>[];
  rows?: Record<string, Cell>[];
  values?: Record<string, Cell>;
}

/** The draft to edit for a document as the server sent it. */
export function draftOf(view: GreenView): GreenDraft {
  const strip = <T extends Record<string, unknown>>(item: T) => {
    const { check: _check, green: _green, ours: _ours, ...cells } = item as Record<string, unknown>;
    return cells as Record<string, Cell>;
  };
  return {
    period: view.period ?? "",
    service: view.service ?? "",
    lines: view.lines?.map((line) => strip(line as unknown as Record<string, unknown>)),
    rows: view.rows?.map((row) => strip(row as unknown as Record<string, unknown>)),
    values: view.values ? { ...view.values } : undefined,
  };
}

const format = (value: number | null | undefined) => (value === null || value === undefined ? "—" : value.toLocaleString());
const mark = (status: string, difference: number | null) =>
  status === "incomplete" ? "missing" : `${status === "ok" ? "✓" : "⚠"} ${difference ?? ""}`;

interface Props {
  view: GreenView;
  draft: GreenDraft;
  editable: boolean;
  /** The company's meters (Settings → Sites); empty when none are set. */
  ourMeters: Set<string>;
  onChange: (draft: GreenDraft) => void;
}

/** A green power document: a retailer's statement or notice, or Taipower's transfer fee list. */
export function GreenTable({ view, draft, editable, ourMeters, onChange }: Props) {
  return (
    <div className="green-table">
      <fieldset>
        <legend>{view.type_name}</legend>
        <div className="green-head">
          <label className="green-period">
            <span>Period 期間</span>
            <input value={draft.period} readOnly={!editable} placeholder="2026/08/01~2026/08/31"
              onChange={(event) => onChange({ ...draft, period: event.target.value })} />
          </label>
          {view.type !== "statement" && (
            <label className="green-period">
              <span>Service 服務編號</span>
              <input value={draft.service ?? ""} readOnly={!editable} placeholder="1-11210-08-00210"
                onChange={(event) => onChange({ ...draft, service: event.target.value })} />
            </label>
          )}
        </div>
        {view.type === "statement" && <StatementLines view={view} draft={draft} editable={editable} onChange={onChange} />}
        {view.type === "transfer_list" && <TransferRows draft={draft} editable={editable} ourMeters={ourMeters} onChange={onChange} />}
        {view.type === "notice" && <NoticeValues draft={draft} editable={editable} onChange={onChange} />}
      </fieldset>
      <Comparisons view={view} />
    </div>
  );
}

// ------------------------------------------------------------- statement
const LINE_COLUMNS: { key: string; label: string; numeric: boolean; width?: number }[] = [
  { key: "meter", label: "電號", numeric: false, width: 96 },
  { key: "energy", label: "能源別", numeric: false, width: 64 },
  { key: "base", label: "基準價", numeric: true },
  { key: "transfer_rate", label: "轉供費率", numeric: true },
  { key: "certificate_rate", label: "憑證費率", numeric: true },
  { key: "kwh", label: "轉供度數", numeric: true },
  { key: "cost", label: "本月電費", numeric: true },
];

function StatementLines({ view, draft, editable, onChange }: Omit<Props, "ourMeters">) {
  const lines = draft.lines ?? [];
  const setCell = (index: number, key: string, value: string) =>
    onChange({ ...draft, lines: lines.map((line, at) => (at === index ? { ...line, [key]: value } : line)) });
  const checks = lines.map((line) => checkLine(line, view.tolerance));
  const totalKwh = lines.reduce<number>((sum, line) => sum + (isGreen(line) ? cellNumber(line.kwh) ?? 0 : 0), 0);
  const totalCost = lines.reduce<number>((sum, line) => sum + (cellNumber(line.cost) ?? 0), 0);
  const totalExpected = checks.reduce<number>((sum, check) => sum + (check.expected ?? 0), 0);
  return (
    <>
      <div className="green-scroll">
        <table>
          <thead>
            <tr>
              {LINE_COLUMNS.map((column) => <th key={column.key}>{column.label}</th>)}
              <th title="kWh × (基準價 + 轉供費率 + 憑證費率), rounded">計算金額</th>
              <th title={`Allowed: ±${view.tolerance} NT$ (Settings)`}>差額</th>
              {editable && <th aria-label="Remove" />}
            </tr>
          </thead>
          <tbody>
            {lines.map((line, index) => (
              <tr key={index} className={checks[index].status}>
                {LINE_COLUMNS.map((column) => (
                  <td key={column.key}>
                    <input className={column.numeric ? "num" : ""} style={column.width ? { width: column.width } : undefined}
                      value={line[column.key] ?? ""} readOnly={!editable} inputMode={column.numeric ? "decimal" : "text"}
                      onChange={(event) => setCell(index, column.key, event.target.value)} />
                  </td>
                ))}
                <td className="num">{format(checks[index].expected)}</td>
                <td className={`num diff ${checks[index].status}`}>{mark(checks[index].status, checks[index].difference)}</td>
                {editable && (
                  <td>
                    <button type="button" className="icon" aria-label={`Remove line ${index + 1}`}
                      onClick={() => onChange({ ...draft, lines: lines.filter((_, at) => at !== index) })}>✕</button>
                  </td>
                )}
              </tr>
            ))}
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
        <button type="button" className="link" onClick={() => onChange({ ...draft, lines: [...lines, { meter: "", energy: "", kwh: "", cost: "" }] })}>
          + Line
        </button>
      )}
      <p className="hint">
        The statement&apos;s 本月電費 are what is reported. The retailer adds Taipower&apos;s transfer fees, each rounded row by row,
        so a few NT$ from kWh × rates is normal; the printed 每度單價 is only 本月電費 ÷ kWh.
      </p>
    </>
  );
}

// ------------------------------------------------------ Taipower's list
const ROW_COLUMNS: { key: string; label: string; numeric: boolean; width?: number }[] = [
  { key: "user_meter", label: "用戶電號", numeric: false, width: 96 },
  { key: "generator", label: "發電電號", numeric: false, width: 96 },
  { key: "kwh", label: "轉供度數", numeric: true },
  { key: "rate", label: "費率", numeric: true, width: 56 },
  { key: "fee", label: "費用", numeric: true },
];

// Taipower's fee tables, offered when one is added by hand.
const FEE_TABLES = ["轉供輸電費清單", "轉供配電費清單", "轉供輔助服務費清單", "轉供電力調度費清單"];

function TransferRows({ draft, editable, ourMeters, onChange }: Omit<Props, "view">) {
  const [newTable, setNewTable] = useState("");
  const rows = draft.rows ?? [];
  const setRows = (next: Record<string, Cell>[]) => onChange({ ...draft, rows: next });
  const setCell = (index: number, key: string, value: string) =>
    setRows(rows.map((row, at) => (at === index ? { ...row, [key]: value } : row)));
  const isOurs = (row: Record<string, Cell>) => ourMeters.size === 0 || ourMeters.has(meterNumber(row.user_meter));
  const sections = [...new Set(rows.map((row) => String(row.section ?? "")))];
  const ours = rows.filter(isOurs);
  /** A new row at the end of a fee table, with the table's usual rate. */
  const addRow = (section: string) => {
    const inTable = rows.filter((row) => String(row.section ?? "") === section);
    const rates = inTable.map((row) => String(row.rate ?? "")).filter(Boolean);
    const usual = rates.sort((a, b) => rates.filter((rate) => rate === b).length - rates.filter((rate) => rate === a).length)[0] ?? "";
    const last = rows.map((row) => String(row.section ?? "")).lastIndexOf(section);
    const row: Record<string, Cell> = { section, user_meter: "", generator: inTable[inTable.length - 1]?.generator ?? "", kwh: "", rate: usual, fee: "" };
    setRows(last < 0 ? [...rows, row] : [...rows.slice(0, last + 1), row, ...rows.slice(last + 1)]);
  };
  const addTable = () => {
    const name = newTable.trim();
    if (!name) return;
    addRow(name);
    setNewTable("");
  };
  return (
    <>
      {ourMeters.size === 0 && (
        <p className="hint warn-text">No meters set in Settings → Sites: every row counts as the company&apos;s.</p>
      )}
      {rows.length === 0 && (
        <p className="hint warn-text">No rows read. Add a fee table below, then its rows (one per user meter and generator).</p>
      )}
      <div className="green-scroll">
        <table>
          <thead>
            <tr>
              <th>Ours</th>
              {ROW_COLUMNS.map((column) => <th key={column.key}>{column.label}</th>)}
              <th title="Taipower's fee is exactly kWh × rate, rounded">核對</th>
              {editable && <th aria-label="Remove" />}
            </tr>
          </thead>
          {sections.map((section) => (
            <tbody key={section}>
              <tr className="section">
                <td colSpan={ROW_COLUMNS.length + 2}>
                  {section || "—"}
                  {editable && <button type="button" className="link" onClick={() => addRow(section)}>+ Row</button>}
                </td>
                {editable && <td />}
              </tr>
              {rows.map((row, index) => {
                if (String(row.section ?? "") !== section) return null;
                const check = checkRow(row);
                return (
                  <tr key={index} className={[check.status, isOurs(row) ? "" : "not-ours"].join(" ")}>
                    <td>{isOurs(row) ? "✓" : ""}</td>
                    {ROW_COLUMNS.map((column) => (
                      <td key={column.key}>
                        <input className={column.numeric ? "num" : ""} style={column.width ? { width: column.width } : undefined}
                          value={row[column.key] ?? ""} readOnly={!editable} inputMode={column.numeric ? "decimal" : "text"}
                          onChange={(event) => setCell(index, column.key, event.target.value)} />
                      </td>
                    ))}
                    <td className={`num diff ${check.status}`} title={row.note ? String(row.note) : undefined}>
                      {mark(check.status, check.difference)}{row.note ? " ✎" : ""}
                    </td>
                    {editable && (
                      <td>
                        <button type="button" className="icon" aria-label={`Remove row ${index + 1}`}
                          onClick={() => setRows(rows.filter((_, at) => at !== index))}>✕</button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          ))}
          <tfoot>
            <tr>
              <td colSpan={5}>Our meters: fees in total</td>
              <td className="num">{format(ours.reduce<number>((sum, row) => sum + (cellNumber(row.fee) ?? 0), 0))}</td>
              <td />
              {editable && <td />}
            </tr>
          </tfoot>
        </table>
      </div>
      {editable && (
        <div className="add-table">
          <input list="fee-tables" value={newTable} placeholder="Fee table, e.g. 轉供輸電費清單"
            onChange={(event) => setNewTable(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addTable(); } }} />
          <datalist id="fee-tables">
            {FEE_TABLES.filter((name) => !sections.includes(name)).map((name) => <option key={name} value={name} />)}
          </datalist>
          <button type="button" onClick={addTable} disabled={!newTable.trim()}>+ Fee table</button>
        </div>
      )}
      <p className="hint">
        Taipower&apos;s transferred kWh are the reference for green power; its fees are kWh × rate rounded, so each row must match
        exactly. ✎: a value was put right or not read (hover for why). Rows OCR missed can be added with + Row.
      </p>
    </>
  );
}

// ------------------------------------------------------- purchase notice
const NOTICE_FIELDS: { key: string; label: string }[] = [
  { key: "purchase_rate", label: "購售費率 A" },
  { key: "kwh", label: "實際購售電量 B (kWh)" },
  { key: "purchase_charge", label: "實際購售電量電費 A×B" },
  { key: "fit_rate", label: "台電躉購費率 C" },
  { key: "surplus_kwh", label: "餘電電量 D (kWh)" },
  { key: "surplus_charge", label: "餘電電費 (A−C)×D" },
  { key: "supply_fees", label: "電能供應費用 E" },
  { key: "certificate_fee", label: "憑證審查費 F" },
  { key: "certificates", label: "憑證張數" },
  { key: "untaxed", label: "未稅金額合計" },
  { key: "tax", label: "應付稅金" },
  { key: "total", label: "本期應付總額" },
];

function NoticeValues({ draft, editable, onChange }: Omit<Props, "view" | "ourMeters">) {
  const values = draft.values ?? {};
  const checks = Object.fromEntries(checkNotice(values).map((check) => [check.field, check]));
  return (
    <>
      <div className="notice-grid">
        {NOTICE_FIELDS.map((field) => {
          const check = checks[field.key];
          return (
            <label key={field.key} className={check ? `notice-field ${check.status}` : "notice-field"}>
              <span>{field.label}</span>
              <input className="num" value={values[field.key] ?? ""} readOnly={!editable} inputMode="decimal"
                onChange={(event) => onChange({ ...draft, values: { ...values, [field.key]: event.target.value } })} />
              {check && (
                <small className={`diff ${check.status}`} title={check.expected !== null ? `${check.label} = ${format(check.expected)}` : undefined}>
                  {check.status === "incomplete" ? "—" : check.status === "ok" ? `✓ ${check.label}` : `⚠ ${check.label}: ${format(check.expected)} (${check.difference})`}
                </small>
              )}
            </label>
          );
        })}
      </div>
      <p className="hint">The notice&apos;s untaxed total (A×B + (A−C)×D + E + F) is the green power cost; E should equal Taipower&apos;s fees for our meters.</p>
    </>
  );
}

// ---------------------------------------------------------- comparisons
const HOW: Record<string, string> = {
  "same period": "",
  prorated: " (bill prorated by days)",
  none: "no Taipower bill",
  "no period": "no period",
};

function difference(value: number | null) {
  if (value === null) return "—";
  return value === 0 ? "✓ 0" : `⚠ ${value.toLocaleString()}`;
}

function Comparisons({ view }: { view: GreenView }) {
  const { meters, services, sites_set: sitesSet } = view.comparisons;
  return (
    <fieldset>
      <legend>How it agrees with the other documents (as saved)</legend>
      {!sitesSet && <p className="hint warn-text">Set the company&apos;s meters in Settings → Sites so only your rows of Taipower&apos;s lists count.</p>}
      {services.length > 0 && <ServiceTable services={services} />}
      {meters.length > 0 && <MeterTable meters={meters} />}
      {meters.length === 0 && services.length === 0 && <p className="hint">Nothing to compare yet: upload Taipower&apos;s transfer fee list or bills for the same month.</p>}
    </fieldset>
  );
}

function ServiceTable({ services }: { services: ServiceComparison[] }) {
  return (
    <>
    <table className="crosscheck">
      <thead>
        <tr><th>Month</th><th>Service</th><th>Taipower kWh</th><th>Notice B</th><th>Diff</th><th>Taipower fees</th><th>Notice E</th><th>Diff</th></tr>
      </thead>
      <tbody>
        {services.map((service) => (
          <tr key={`${service.month}-${service.service}`}>
            <td>{service.month}</td>
            <td>{service.service ?? "—"}</td>
            <td className="num">{service.has_list ? format(service.taipower_list_kwh) : "no list"}</td>
            <td className="num">{format(service.notice?.kwh)}</td>
            <td className="num">{difference(service.kwh_difference)}</td>
            <td className="num" title={service.sections.join("、")}>
              {service.has_list ? format(service.taipower_list_fees) : "—"}
              {service.has_list && <small className="sections"><br />{service.sections.map((name) => name.replace(/^轉供|費清單$/g, "")).join("、")}</small>}
            </td>
            <td className="num">{format(service.notice?.supply_fees)}</td>
            <td className="num">{difference(service.fee_difference)}</td>
          </tr>
        ))}
      </tbody>
    </table>
    {services.some((service) => service.fee_difference) && (
      <p className="hint">
        E covers every fee table of Taipower&apos;s list (輸電, 配電, 輔助服務, 調度 ...): when E differs, check that every page of the list
        was uploaded; the fee tables read are shown under Taipower fees.
      </p>
    )}
    </>
  );
}

function MeterTable({ meters }: { meters: MeterComparison[] }) {
  return (
    <table className="crosscheck">
      <thead>
        <tr><th>Month</th><th>電號</th><th>Site</th><th>Taipower list</th><th>Retailer</th><th>Diff</th><th>Bill</th><th>Diff</th></tr>
      </thead>
      <tbody>
        {meters.map((entry) => (
          <tr key={`${entry.month}-${entry.meter}`}>
            <td>{entry.month}</td>
            <td>{entry.meter}</td>
            <td>{entry.site ?? "—"}</td>
            <td className="num">{format(entry.taipower_list_kwh)}</td>
            <td className="num">{format(entry.retailer_kwh)}</td>
            <td className="num">{difference(entry.retailer_difference)}</td>
            <td className="num" title={entry.bills.map((bill) => `${bill.filename} ${bill.period ?? ""}`).join(", ")}>
              {entry.bill_kwh === null ? HOW[entry.bill_how] : `${format(entry.bill_kwh)}${HOW[entry.bill_how]}`}
            </td>
            <td className="num">{difference(entry.taipower_list_kwh === null ? entry.retailer_bill_difference : entry.bill_difference)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
