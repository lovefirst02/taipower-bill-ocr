import { useMemo, useState } from "react";
import { overview } from "../budgetOverview";
import type { MonthPoint, Status } from "../budgetOverview";
import type { Plan, PlanEnergy } from "../types";

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ENERGY_LABELS: Record<string, string> = { grey: "Grey 灰電", solar: "Solar 太陽能", wind: "Wind 風力" };
const money = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `NT$${Math.round(value).toLocaleString()}`);
const short = (value: number) => (Math.abs(value) >= 1_000_000 ? `${(value / 1_000_000).toFixed(1)}M` : Math.abs(value) >= 1000 ? `${Math.round(value / 1000)}k` : `${Math.round(value)}`);
const percent = (value: number | null) => (value === null ? "—" : `${(value * 100).toFixed(1)}%`);

const STATUS: Record<Status, { icon: string; label: string }> = {
  "on track": { icon: "✓", label: "On track" },
  "at risk": { icon: "⚠", label: "At risk" },
  over: { icon: "✕", label: "Over budget" },
  none: { icon: "", label: "No budget" },
};

/** A status always carries its icon and label, never color alone. */
function StatusBadge({ value }: { value: Status }) {
  const { icon, label } = STATUS[value];
  return <span className={`status-badge ${value.replace(" ", "-")}`}>{icon && <i aria-hidden>{icon}</i>}{label}</span>;
}

interface Props {
  plan: Plan;
  onOpenSite: (site: string) => void;
}

/** The budget at a glance: where the year stands, how it is going, and which sites need a look. */
export function BudgetOverview({ plan, onOpenSite }: Props) {
  const [entity, setEntity] = useState("all");
  const [energy, setEnergy] = useState<PlanEnergy | "all">("all");
  const [siteName, setSiteName] = useState("all");
  const entities = useMemo(() => [...new Set(plan.sites.map((site) => site.entity ?? "(no entity)"))].sort(), [plan]);
  // The sites of the entity chosen; a site no longer among them goes back to all.
  const siteNames = useMemo(() => plan.sites.filter((site) => entity === "all" || (site.entity ?? "(no entity)") === entity)
    .map((site) => site.site), [plan, entity]);
  const chosenSite = siteNames.includes(siteName) ? siteName : "all";
  const view = useMemo(() => overview(plan, entity, energy, chosenSite), [plan, entity, energy, chosenSite]);
  const { total } = view;
  const now = new Date();
  const elapsed = plan.year < now.getFullYear() ? 1 : plan.year > now.getFullYear() ? 0 : (view.lastClosed + 1) / 12;

  return (
    <>
      <div className="budget-filters">
        {entities.some((name) => name !== "(no entity)") && (
          <label>Entity <select value={entity} onChange={(event) => setEntity(event.target.value)}>
            <option value="all">All entities</option>
            {entities.map((name) => <option key={name}>{name}</option>)}
          </select></label>
        )}
        <label>Site <select value={chosenSite} onChange={(event) => setSiteName(event.target.value)}>
          <option value="all">All sites</option>
          {siteNames.map((name) => <option key={name}>{name}</option>)}
        </select></label>
        <div className="segmented">
          {(["all", "grey", "solar", "wind"] as const).map((key) => (
            <button key={key} type="button" className={key === energy ? "active" : ""} onClick={() => setEnergy(key)}>
              {key === "all" ? "All energy" : ENERGY_LABELS[key]}
            </button>
          ))}
        </div>
      </div>

      <div className="budget-kpis">
      <div className="kpis">
        <div className="kpi"><span className="kpi-label">Annual budget</span><strong>{money(total.budget)}</strong>
          <small>{plan.year}</small></div>
        <div className="kpi"><span className="kpi-label">Spent so far</span><strong>{money(total.actual)}</strong>
          <small>{percent(total.used)} of the budget · {Math.round(elapsed * 12)} of 12 months in</small></div>
        <div className="kpi"><span className="kpi-label">Year-end forecast</span><strong>{money(total.forecast)}</strong>
          <small>{total.budget ? `${total.variance > 0 ? "+" : "−"}${money(Math.abs(total.variance))} vs budget` : "no budget set"}</small></div>
        <div className="kpi"><span className="kpi-label">Status</span><strong><StatusBadge value={total.status} /></strong>
          <small>{total.budget ? `left to spend ${money(total.budget - total.actual)}` : ""}</small></div>
      </div>

      {energy === "all" && (
        <div className="energy-cards">
          {view.byEnergy.map((item) => (
            <div key={item.name} className="kpi">
              <span className="kpi-label">{ENERGY_LABELS[item.name]}</span>
              <strong>{money(item.forecast)}</strong>
              <small>forecast · budget {money(item.budget)} · spent {money(item.actual)}</small>
              <StatusBadge value={item.status} />
            </div>
          ))}
        </div>
      )}
      </div>

      <section className="dash-card">
        <h3>Spending through the year (cumulative)</h3>
        <BurnUp months={view.months} budget={total.budget} lastClosed={view.lastClosed} />
      </section>

      <section className="dash-card">
        <h3>Sites — the most over budget first</h3>
        <div className="green-scroll">
          <table className="dash-table budget-sites">
            <thead>
              <tr><th>Site</th><th>Entity</th><th>Budget</th><th>Spent</th><th>Used</th><th aria-label="Spent and forecast against the budget" /><th>Year-end forecast</th><th>vs budget</th><th>Status</th></tr>
            </thead>
            <tbody>
              {view.bySite.map((item) => {
                const scale = Math.max(item.budget, item.forecast, 1);
                return (
                  <tr key={item.name}>
                    <td><button type="button" className="link" onClick={() => onOpenSite(item.name)} title="Plan this site month by month">{item.name}</button></td>
                    <td>{item.entity ?? "—"}</td>
                    <td className="num">{money(item.budget)}</td>
                    <td className="num">{money(item.actual)}</td>
                    <td className="num">{percent(item.used)}</td>
                    <td className="bar-cell">
                      <span className="spend-bar" title={`Spent ${money(item.actual)} · forecast ${money(item.forecast)} · budget ${money(item.budget)}`}>
                        <i style={{ width: `${(item.actual / scale) * 100}%` }} />
                        <u style={{ left: `${(Math.min(item.forecast, scale) / scale) * 100}%` }} />
                        {item.budget > 0 && <b style={{ left: `${(item.budget / scale) * 100}%` }} />}
                      </span>
                    </td>
                    <td className="num">{money(item.forecast)}</td>
                    <td className={`num ${item.variance > 0 && item.budget ? "over" : ""}`}>{item.budget ? `${item.variance > 0 ? "+" : "−"}${money(Math.abs(item.variance))}` : "—"}</td>
                    <td><StatusBadge value={item.status} /></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="hint">
          Bar: spent so far (blue), year-end forecast (dashed tick), budget (black tick). On track: the forecast is within the budget; at risk: up
          to 5% over; over budget: more. Click a site to plan it month by month.
        </p>
      </section>
    </>
  );
}

/** Cumulative planned budget, actual spending to date, and the forecast on to year-end, against the annual budget. */
function BurnUp({ months, budget, lastClosed }: { months: MonthPoint[]; budget: number; lastClosed: number }) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 860, height = 260, left = 64, right = 70, top = 16, bottom = 28;
  const highest = Math.max(budget, ...months.map((month) => Math.max(month.cumulativePlanned, month.cumulativeForecast, month.cumulativeActual ?? 0)), 1) * 1.08;
  const x = (index: number) => left + ((width - left - right) * (index + 0.5)) / 12;
  const y = (value: number) => top + (height - top - bottom) * (1 - value / highest);
  const path = (points: [number, number][]) => points.map(([px, py], index) => `${index ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`).join("");
  const planned = path(months.map((month, index) => [x(index), y(month.cumulativePlanned)]));
  const actual = lastClosed >= 0 ? path(months.slice(0, lastClosed + 1).map((month, index) => [x(index), y(month.cumulativeActual ?? 0)])) : "";
  // The forecast carries on from the last month with documents (or starts in January).
  const from = Math.max(lastClosed, 0);
  const forecast = path(months.slice(from).map((month, index) => [x(from + index), y(month.cumulativeForecast)]));
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((part) => (highest / 1.08) * part);
  const shown = hover !== null ? months[hover] : null;
  return (
    <div className="burnup">
      <div className="legend">
        <span><i className="swatch actual" />Actual</span>
        <span><i className="swatch forecast" />Forecast</span>
        <span><i className="swatch planned" />Planned budget</span>
        {budget > 0 && <span><i className="swatch budget" />Annual budget</span>}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="chart" role="img" aria-label="Cumulative spending against the budget"
        onMouseLeave={() => setHover(null)}>
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} className="grid" />
            <text x={left - 6} y={y(tick) + 4} textAnchor="end" className="axis">{short(tick)}</text>
          </g>
        ))}
        {budget > 0 && (
          <g>
            <line x1={left} x2={width - right} y1={y(budget)} y2={y(budget)} className="budget-line" />
            <text x={width - right + 4} y={y(budget) + 4} className="label">Budget</text>
          </g>
        )}
        <path d={planned} className="line planned" />
        <path d={forecast} className="line forecast" />
        {actual && <path d={actual} className="line actual" />}
        {months.slice(0, lastClosed + 1).map((month, index) => (
          <circle key={month.month} cx={x(index)} cy={y(month.cumulativeActual ?? 0)} r={4} className="dot actual" />
        ))}
        <text x={width - right + 4} y={y(months[11].cumulativeForecast) + 4} className="label">Forecast</text>
        {months.map((month, index) => (
          <g key={month.month}>
            <text x={x(index)} y={height - 8} textAnchor="middle" className="axis">{MONTH_NAMES[index]}</text>
            <rect x={x(index) - (width - left - right) / 24} width={(width - left - right) / 12} y={top} height={height - top - bottom}
              className="hit" onMouseEnter={() => setHover(index)} />
          </g>
        ))}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={top} y2={height - bottom} className="crosshair" />}
      </svg>
      {shown && hover !== null && (
        <div className="chart-tip" style={{ left: `${(x(hover) / width) * 100}%` }}>
          <strong>{MONTH_NAMES[hover]} (to date)</strong>
          {shown.cumulativeActual !== null && <span>Actual {money(shown.cumulativeActual)}</span>}
          <span>Forecast {money(shown.cumulativeForecast)}</span>
          <span>Planned {money(shown.cumulativePlanned)}</span>
          <small>This month: {shown.actual !== null ? `actual ${money(shown.actual)}` : `forecast ${money(shown.forecast)}`} · planned {money(shown.planned)}</small>
        </div>
      )}
    </div>
  );
}
