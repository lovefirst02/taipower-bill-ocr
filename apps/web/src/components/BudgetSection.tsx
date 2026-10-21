import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { compare, METRICS, overall } from "../budget";
import type { Comparison } from "../budget";
import type { BudgetRow, MixRow } from "../types";

type Grouping = "month" | "site" | "entity";
const GROUPINGS: Record<Grouping, string> = { month: "Month", site: "Site", entity: "Entity" };

const amount = (value: number | null, unit: "kWh" | "NT$") =>
  value === null ? "—" : unit === "NT$" ? `NT$${Math.round(value).toLocaleString()}` : `${Math.round(value).toLocaleString()} kWh`;
const signed = (value: number | null, unit: "kWh" | "NT$") =>
  value === null ? "—" : `${value > 0 ? "+" : value < 0 ? "−" : ""}${amount(Math.abs(value), unit)}`;
const percent = (value: number | null) => (value === null ? "—" : `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)}%`);

interface Props {
  /** The actuals shown on the dashboard (its year, entity and site). */
  actuals: MixRow[];
  year: string;
  entity: string;
  site: string;
}

/** Budget against actual: by month, site or entity, for one figure at a time. */
export function BudgetSection({ actuals, year, entity, site }: Props) {
  const [budgets, setBudgets] = useState<BudgetRow[]>([]);
  const [canEdit, setCanEdit] = useState(false);
  const [metricKey, setMetricKey] = useState("total_cost");
  const [grouping, setGrouping] = useState<Grouping>("month");
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  const load = () => api.budget().then((result) => { setBudgets(result.rows); setCanEdit(result.can_edit); }).catch(() => undefined);
  useEffect(() => { void load(); }, []);

  const templateYear = year !== "all" ? Number(year) : new Date().getFullYear();
  const shown = budgets.filter((row) => (year === "all" || row.month.startsWith(year))
    && (entity === "all" || (row.entity ?? "(no entity)") === entity) && (site === "all" || row.site === site));
  const metric = METRICS.find((item) => item.key === metricKey) ?? METRICS[0];
  const key = (row: { month: string; site: string | null; entity: string | null }) =>
    grouping === "month" ? row.month : grouping === "site" ? row.site ?? "(no site)" : row.entity ?? "(no entity)";
  const items = compare(metric, actuals, shown, key);
  const total = overall(items);

  const importFile = async (chosen: File | undefined) => {
    if (!chosen) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.importBudget(chosen);
      setMessage({ kind: "ok", text: `Imported ${result.imported} row(s) for ${result.months.join(", ")}.` });
      await load();
    } catch (error) {
      setMessage({ kind: "error", text: (error as Error).message });
    } finally {
      setBusy(false);
      if (file.current) file.current.value = "";
    }
  };

  return (
    <section className="dash-card budget">
      <div className="budget-head">
        <h3>Budget vs actual</h3>
        <a className="button" href={api.budgetTemplateUrl(templateYear)} title="A row per month and site, with what is budgeted already">
          Budget template {templateYear}
        </a>
        {canEdit && (
          <>
            <button type="button" disabled={busy} onClick={() => file.current?.click()}>Import budget…</button>
            <input ref={file} type="file" accept=".xlsx" hidden onChange={(event) => void importFile(event.target.files?.[0])} />
          </>
        )}
      </div>
      {message && <p className={`message ${message.kind}`} style={{ whiteSpace: "pre-line" }}>{message.text}</p>}
      {budgets.length === 0 ? (
        <p className="hint">
          No budget yet. Download the template, fill in each month and site (green costs may be left empty: they are estimated from
          Settings → Green power contracts), then import it{canEdit ? "" : " on the computer running the server"}.
        </p>
      ) : (
        <>
          <div className="budget-controls">
            <div className="segmented">
              {METRICS.map((item) => (
                <button key={item.key} type="button" className={item.key === metricKey ? "active" : ""} onClick={() => setMetricKey(item.key)}>{item.label}</button>
              ))}
            </div>
            <label>By <select value={grouping} onChange={(event) => setGrouping(event.target.value as Grouping)}>
              {Object.entries(GROUPINGS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select></label>
          </div>
          <div className="green-scroll">
            <table className="dash-table budget-table">
              <thead>
                <tr><th>{GROUPINGS[grouping]}</th><th className="num">Budget</th><th className="num">Actual</th><th className="num">Variance</th><th className="num">%</th><th aria-label="Actual against budget" /></tr>
              </thead>
              <tbody>
                {[...items, ...(items.length > 1 ? [total] : [])].map((item) => <BudgetLine key={item.name} item={item} unit={metric.unit} total={item === total} />)}
              </tbody>
            </table>
          </div>
          <p className="hint">
            Actual: bills&apos; pretax amounts and retailers&apos; untaxed amounts, as above. Variance = actual − budget (over budget is red).
            {total.full_budget !== null && total.full_budget !== total.budget && ` Whole budget shown: ${amount(total.full_budget ?? null, metric.unit)}.`}
            {total.estimated && " * part of the budget is estimated from a contract price."}
          </p>
        </>
      )}
    </section>
  );
}

function BudgetLine({ item, unit, total }: { item: Comparison; unit: "kWh" | "NT$"; total: boolean }) {
  const share = item.budget ? Math.min(item.actual / item.budget, 1.5) : null;
  const over = item.variance !== null && item.variance > 0;
  return (
    <tr className={total ? "total" : ""}>
      <td>{total ? "Total (with documents)" : item.name}</td>
      <td className="num">{amount(item.budget, unit)}{item.estimated ? " *" : ""}</td>
      <td className="num">{item.has_actual ? amount(item.actual, unit) : <span className="hint">no documents yet</span>}</td>
      <td className={`num ${over ? "over" : item.variance !== null ? "under" : ""}`}>{signed(item.variance, unit)}</td>
      <td className={`num ${over ? "over" : item.variance !== null ? "under" : ""}`}>{percent(item.ratio)}</td>
      <td className="bar-cell">
        {share !== null && (
          <span className="budget-bar" title={`${(share * 100).toFixed(0)}% of budget`}>
            <i className={over ? "over" : ""} style={{ width: `${(Math.min(share, 1.5) / 1.5) * 100}%` }} />
            <b style={{ left: `${(1 / 1.5) * 100}%` }} />
          </span>
        )}
      </td>
    </tr>
  );
}
