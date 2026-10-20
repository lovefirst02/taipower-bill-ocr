import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { EnergyPlan, Plan, PlanEnergy, SitePlan } from "../types";
import { BudgetOverview } from "./BudgetOverview";

const ENERGIES: { key: PlanEnergy; label: string }[] = [
  { key: "grey", label: "Grey 灰電" },
  { key: "solar", label: "Solar 太陽能" },
  { key: "wind", label: "Wind 風力" },
];
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const SOURCES: Record<string, string> = {
  "last year": "去年同月實際", "last year avg": "去年平均月（去年同月沒有資料）", "this year avg": "今年已有月份的平均（沒有去年資料）",
  "this year": "今年實際平均單價", contract: "合約（價格＋轉供費＋憑證費）", manual: "",
};
const ALLOCATIONS: Record<string, string> = {
  estimate: "shared by the estimates",
  "estimate + average": "shared by the estimates; months without one weigh as the average month",
  even: "no estimates: shared evenly over 12 months",
};

const money = (value: number | null | undefined) => (value === null || value === undefined ? "—" : Math.round(value).toLocaleString());
const number = (text: string): number | null => {
  const cleaned = text.replace(/[,\s%]/g, "");
  if (!cleaned) return null;
  const value = Number(cleaned);
  return Number.isFinite(value) ? value : null;
};

/** The electricity budget: each site's annual budget per energy, planned month by month, against the actuals. */
export function BudgetPlanner() {
  const [year, setYear] = useState(new Date().getFullYear());
  const [plan, setPlan] = useState<Plan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);
  const [siteName, setSiteName] = useState<string | null>(null);
  const [energy, setEnergy] = useState<PlanEnergy>("grey");
  const [tab, setTab] = useState<"overview" | "plan">("overview");
  const file = useRef<HTMLInputElement>(null);

  const load = (chosen = year) => {
    setError(null);
    api.plan(chosen).then(setPlan).catch((reason: Error) => setError(reason.message));
  };
  useEffect(() => load(year), [year]);

  const save = async (update: Parameters<typeof api.savePlan>[0], done: string) => {
    setMessage(null);
    try {
      setPlan(await api.savePlan(update));
      setMessage({ kind: "ok", text: done });
    } catch (reason) {
      setMessage({ kind: "error", text: (reason as Error).message });
    }
  };
  const importFile = async (chosen: File | undefined) => {
    if (!chosen) return;
    setMessage(null);
    try {
      const result = await api.importPlan(year, chosen);
      setMessage({ kind: "ok", text: `Imported ${result.imported} budget row(s) for ${year}.` });
      load();
    } catch (reason) {
      setMessage({ kind: "error", text: (reason as Error).message });
    } finally {
      if (file.current) file.current.value = "";
    }
  };

  if (error) return <div className="dashboard"><p className="notice">Could not load the budget: {error}</p></div>;
  if (!plan) return <div className="dashboard"><p className="hint">Loading…</p></div>;
  if (!plan.sites.length) {
    return <div className="dashboard"><div className="welcome"><h2>No sites yet</h2><p>Set the sites in Settings → Sites first: the budget is planned per site.</p></div></div>;
  }
  const site = plan.sites.find((item) => item.site === siteName) ?? plan.sites[0];
  const current = new Date();
  const lastClosed = year < current.getFullYear() ? 11 : year > current.getFullYear() ? -1 : current.getMonth() - 1;
  const totals = ENERGIES.map(({ key }) => plan.sites.reduce((sum, item) => sum + item.energies[key].annual, 0));
  const actualTotal = plan.sites.reduce((sum, item) => sum + ENERGIES.reduce((inner, { key }) => inner + item.energies[key].actual_cost, 0), 0);
  const annualTotal = totals.reduce((sum, value) => sum + value, 0);
  const forecastTotal = plan.sites.reduce((sum, item) => sum + ENERGIES.reduce((inner, { key }) => inner + item.energies[key].forecast.year_end, 0), 0);
  const balanceNow = lastClosed >= 0
    ? plan.sites.reduce((sum, item) => sum + ENERGIES.reduce((inner, { key }) => inner + item.energies[key].months[lastClosed].balance, 0), 0)
    : null;

  return (
    <div className="dashboard planner">
      <div className="dash-head">
        <h2>Electricity budget</h2>
        <label>Year <select value={year} onChange={(event) => setYear(Number(event.target.value))}>
          {[-1, 0, 1, 2].map((offset) => current.getFullYear() + offset).map((option) => <option key={option}>{option}</option>)}
        </select></label>
        <a className="button" href={api.planTemplateUrl(year)} title="Every site and energy, month by month, to fill in; the defaults shown beside">Excel {year}</a>
        {plan.can_edit && (
          <>
            <button type="button" onClick={() => file.current?.click()}>Import Excel…</button>
            <input ref={file} type="file" accept=".xlsx" hidden onChange={(event) => void importFile(event.target.files?.[0])} />
          </>
        )}
      </div>
      {message && <p className={`message ${message.kind}`} style={{ whiteSpace: "pre-line" }}>{message.text}</p>}
      <div className="segmented page-tabs">
        <button type="button" className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>Overview</button>
        <button type="button" className={tab === "plan" ? "active" : ""} onClick={() => setTab("plan")}>Plan</button>
      </div>
      {tab === "overview" ? (
        <BudgetOverview plan={plan} onOpenSite={(name) => { setSiteName(name); setTab("plan"); }} />
      ) : (
      <>
      {!plan.can_edit && <p className="hint">The budget can be changed on the computer running the server; here it can be viewed.</p>}

      <div className="kpis">
        <div className="kpi"><span className="kpi-label">Annual budget</span><strong>NT${money(annualTotal)}</strong>
          <small>{ENERGIES.map(({ label }, index) => `${label.split(" ")[0]} ${money(totals[index])}`).join(" · ")}</small></div>
        <div className="kpi"><span className="kpi-label">Actual cost</span><strong>NT${money(actualTotal)}</strong>
          <small>{annualTotal ? `${((actualTotal / annualTotal) * 100).toFixed(1)}% of the budget used` : ""}</small></div>
        <div className="kpi"><span className="kpi-label">Balance{lastClosed >= 0 ? ` (end of ${MONTH_NAMES[lastClosed]})` : ""}</span>
          <strong className={balanceNow !== null && balanceNow < 0 ? "over" : ""}>{balanceNow === null ? "—" : `NT$${money(balanceNow)}`}</strong>
          <small>planned + carried − adopted</small></div>
        <div className="kpi"><span className="kpi-label">Year-end forecast</span>
          <strong className={forecastTotal > annualTotal ? "over" : ""}>NT${money(forecastTotal)}</strong>
          <small>{annualTotal ? `${forecastTotal > annualTotal ? "over" : "under"} budget by NT$${money(Math.abs(forecastTotal - annualTotal))}` : "actuals so far + this year's trend"}</small></div>
      </div>

      <section className="dash-card">
        <h3>Sites</h3>
        <SitesTable plan={plan} year={year} chosen={site.site} onChoose={setSiteName} onSave={save} />
      </section>

      <section className="dash-card">
        <div className="budget-head">
          <h3>{site.site}{site.entity ? ` · ${site.entity}` : ""}</h3>
          <div className="segmented">
            {ENERGIES.map((item) => (
              <button key={item.key} type="button" className={item.key === energy ? "active" : ""} onClick={() => setEnergy(item.key)}>{item.label}</button>
            ))}
          </div>
        </div>
        <MonthsTable key={`${year}-${site.site}-${energy}-${JSON.stringify(site.energies[energy].months.map((month) => [month.kwh_source, month.kwh, month.rate_source, month.rate]))}-${site.energies[energy].adjustment_pct}`} year={year} site={site} plan={site.energies[energy]} canEdit={plan.can_edit} onSave={save} />
      </section>
      </>
      )}
    </div>
  );
}

function SitesTable({ plan, year, chosen, onChoose, onSave }: {
  plan: Plan; year: number; chosen: string; onChoose: (site: string) => void;
  onSave: (update: Parameters<typeof api.savePlan>[0], done: string) => void;
}) {
  const saveAnnual = (site: SitePlan, energy: PlanEnergy | "total", text: string) => {
    const value = number(text);
    const plan = energy === "total" ? null : site.energies[energy];
    const known = energy === "total" ? site.total_budget : plan?.annual_input ?? null;
    if (value === known) return;
    const months = plan ? Object.fromEntries(plan.months.filter((month) => month.kwh_source === "manual" || month.rate_source === "manual")
      .map((month) => [month.month.slice(5), { kwh: month.kwh_source === "manual" ? month.kwh : null, rate: month.rate_source === "manual" ? month.rate : null }])) : {};
    onSave({ year, site: site.site, energy, annual_budget: value, adjustment_pct: plan?.adjustment_pct ?? 0, months }, `${site.site}: budget saved`);
  };
  return (
    <div className="green-scroll">
      <table className="dash-table plan-sites">
        <thead>
          <tr><th>Site</th><th>Entity</th><th>Site annual budget</th>{ENERGIES.map(({ key, label }) => <th key={key}>{label}</th>)}<th>Sum</th><th>Check</th><th>Actual</th><th>Used</th><th>Year-end forecast</th><th>vs budget</th></tr>
        </thead>
        <tbody>
          {plan.sites.map((site) => {
            const actual = ENERGIES.reduce((sum, { key }) => sum + site.energies[key].actual_cost, 0);
            return (
              <tr key={site.site} className={site.site === chosen ? "chosen" : ""} onClick={() => onChoose(site.site)}>
                <td><button type="button" className="link">{site.site}</button></td>
                <td>{site.entity ?? "—"}</td>
                <td><input key={`total-${site.total_budget}`} className="num" defaultValue={site.total_budget ?? ""} disabled={!plan.can_edit} placeholder="—"
                  onClick={(event) => event.stopPropagation()} onBlur={(event) => saveAnnual(site, "total", event.target.value)} /></td>
                {ENERGIES.map(({ key }) => (
                  <td key={key} title={site.energies[key].method === "estimate" ? "Not set: the estimated costs added up" : "Approved budget"}>
                    <input key={`${key}-${site.energies[key].annual_input}`} className="num" defaultValue={site.energies[key].annual_input ?? ""} disabled={!plan.can_edit}
                      placeholder={money(site.energies[key].annual)} onClick={(event) => event.stopPropagation()}
                      onBlur={(event) => saveAnnual(site, key, event.target.value)} />
                  </td>
                ))}
                <td className="num">{money(site.energies_total)}</td>
                <td className={site.allocation === "check" ? "verdict check" : site.allocation === "ok" ? "verdict ok" : ""}>
                  {site.allocation === "ok" ? "✓" : site.allocation === "check" ? `⚠ ${money((site.total_budget ?? 0) - site.energies_total)}` : ""}
                </td>
                <td className="num">{money(actual)}</td>
                <td className="num">{site.energies_total ? `${((actual / site.energies_total) * 100).toFixed(1)}%` : "—"}</td>
                {(() => {
                  const yearEnd = ENERGIES.reduce((sum, { key }) => sum + site.energies[key].forecast.year_end, 0);
                  const variance = yearEnd - site.energies_total;
                  return (
                    <>
                      <td className="num">{money(yearEnd)}</td>
                      <td className={`num ${variance > 0 ? "over" : ""}`}>{site.energies_total ? `${variance > 0 ? "+" : ""}${money(variance)}` : "—"}</td>
                    </>
                  );
                })()}
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="hint">An energy left empty is budgeted at its estimated costs added up (shown in grey). Check: the site&apos;s annual budget against its energies&apos;. Click a site to plan it month by month.</p>
    </div>
  );
}

function MonthsTable({ year, site, plan, canEdit, onSave }: {
  year: number; site: SitePlan; plan: EnergyPlan; canEdit: boolean;
  onSave: (update: Parameters<typeof api.savePlan>[0], done: string) => void;
}) {
  // Only typed values are kept; an empty cell is the default.
  const [kwh, setKwh] = useState(plan.months.map((month) => (month.kwh_source === "manual" ? String(month.kwh ?? "") : "")));
  const [rate, setRate] = useState(plan.months.map((month) => (month.rate_source === "manual" ? String(month.rate ?? "") : "")));
  const [adjustment, setAdjustment] = useState(plan.adjustment_pct ? String(plan.adjustment_pct) : "");
  const dirty = kwh.some((value, index) => value !== (plan.months[index].kwh_source === "manual" ? String(plan.months[index].kwh ?? "") : ""))
    || rate.some((value, index) => value !== (plan.months[index].rate_source === "manual" ? String(plan.months[index].rate ?? "") : ""))
    || adjustment !== (plan.adjustment_pct ? String(plan.adjustment_pct) : "");
  const save = () => onSave({
    year, site: site.site, energy: plan.energy, annual_budget: plan.annual_input, adjustment_pct: number(adjustment) ?? 0,
    months: Object.fromEntries(plan.months.map((month, index) => [month.month.slice(5), { kwh: number(kwh[index]), rate: number(rate[index]) }])),
  }, `${site.site}: ${plan.energy} plan saved`);
  const row = (label: string, values: (number | null)[], title?: string, className = "") => (
    <tr className={className}>
      <th title={title}>{label}</th>
      {values.map((value, index) => <td key={index} className={`num ${value !== null && value < 0 ? "over" : ""}`}>{money(value)}</td>)}
      <td className="num total">{money(values.reduce<number>((sum, value) => sum + (value ?? 0), 0))}</td>
    </tr>
  );
  return (
    <>
      <div className="plan-controls">
        <span>Annual budget: <strong>NT${money(plan.annual)}</strong> ({plan.method === "approved" ? "approved" : "estimates added up"}; {ALLOCATIONS[plan.allocation]})</span>
        <label>Adjustment % <input className="num short" value={adjustment} disabled={!canEdit} placeholder="0" onChange={(event) => setAdjustment(event.target.value)} /></label>
        <span>Used: {plan.utilization === null ? "—" : `${(plan.utilization * 100).toFixed(1)}%`}</span>
        <span title="Months with documents are their actuals; the others are last year's month × this year's trend (else this year's average month, else the estimate), at this year's actual price">
          Year-end forecast: <strong className={plan.forecast.variance > 0 ? "over" : ""}>NT${money(plan.forecast.year_end)}</strong>
          {" "}({plan.forecast.variance > 0 ? "+" : ""}{money(plan.forecast.variance)} vs budget
          {plan.forecast.trend !== null ? `; kWh ×${plan.forecast.trend.toFixed(3)} on last year` : ""}
          {plan.forecast.price !== null ? `; price ${plan.forecast.price.toFixed(4)}` : ""})
        </span>
        {canEdit && <button type="button" className="primary" disabled={!dirty} onClick={save}>Save</button>}
      </div>
      <div className="green-scroll">
        <table className="dash-table plan-months">
          <thead>
            <tr><th />{MONTH_NAMES.map((name) => <th key={name}>{name}</th>)}<th>Year</th></tr>
          </thead>
          <tbody>
            <tr className="input-row">
              <th title="Typed in, or by default last year's actual kWh of the month">Est. kWh</th>
              {plan.months.map((month, index) => (
                <td key={month.month}>
                  <input className="num" value={kwh[index]} disabled={!canEdit} placeholder={money(month.kwh_source === "manual" ? null : month.kwh)}
                    title={month.kwh_source && month.kwh_source !== "manual" ? `Default: ${SOURCES[month.kwh_source]}` : undefined}
                    onChange={(event) => setKwh(kwh.map((value, at) => (at === index ? event.target.value : value)))} />
                </td>
              ))}
              <td className="num total">{money(plan.months.reduce((sum, month) => sum + (month.kwh ?? 0), 0))}</td>
            </tr>
            <tr className="input-row">
              <th title="Typed in, or by default: grey last year's average actual price; green the contract (price and fees on top)">Est. price</th>
              {plan.months.map((month, index) => (
                <td key={month.month}>
                  <input className="num" value={rate[index]} disabled={!canEdit}
                    placeholder={month.rate_source === "manual" || month.rate === null ? "—" : month.rate.toFixed(4)}
                    title={month.rate_source && month.rate_source !== "manual" ? `Default: ${SOURCES[month.rate_source]}` : undefined}
                    onChange={(event) => setRate(rate.map((value, at) => (at === index ? event.target.value : value)))} />
                </td>
              ))}
              <td />
            </tr>
            {row("Est. cost", plan.months.map((month) => month.estimated_cost), "kWh × price × (1 + adjustment %)")}
            {row("Planned budget", plan.months.map((month) => month.planned), "The annual budget shared by the estimated costs", "strong")}
            {row("Forecast kWh", plan.months.map((month) => month.forecast_kwh), "Actual where documents came in; else last year's month × this year's trend, else this year's average month, else the estimate", "forecast")}
            {row("Forecast cost", plan.months.map((month) => month.forecast_cost), "Actual where documents came in; else forecast kWh × this year's price × (1 + adjustment %)", "forecast")}
            <tr className="forecast strong">
              <th title="What is left of the annual budget (budget − actual so far), shared over the months to come by the forecast. The planned budget does not change.">Adjusted budget</th>
              {plan.months.map((month) => <td key={month.month} className="num">{month.adjusted_budget === null ? "" : money(month.adjusted_budget)}</td>)}
              <td className="num total">{money(plan.forecast.remaining)}</td>
            </tr>
            {row("Actual kWh", plan.months.map((month) => month.actual_kwh), "From the documents")}
            {row("Actual cost", plan.months.map((month) => month.actual_cost), "From the documents")}
            {row("Adopted cost", plan.months.map((month) => month.adopted_cost), "The actual cost when there is one, else the estimate")}
            <tr>
              <th title="January 0, then last month's balance">Prior carry</th>
              {plan.months.map((month) => <td key={month.month} className={`num ${month.prior_carry < 0 ? "over" : ""}`}>{money(month.prior_carry)}</td>)}
              <td />
            </tr>
            <tr>
              <th title="Planned budget + prior carry">Available</th>
              {plan.months.map((month) => <td key={month.month} className="num">{money(month.available)}</td>)}
              <td />
            </tr>
            <tr className="strong">
              <th title="Available − adopted cost">Month-end balance</th>
              {plan.months.map((month) => <td key={month.month} className={`num ${month.balance < 0 ? "over" : ""}`}>{money(month.balance)}</td>)}
              <td />
            </tr>
          </tbody>
        </table>
      </div>
      <p className="hint">
        Grey numbers in the inputs are the defaults (hover for where they come from); type to override, clear to go back to the default.
        Months with documents use the actual cost; the others the estimate. A negative balance (red) is over budget.
      </p>
    </>
  );
}
