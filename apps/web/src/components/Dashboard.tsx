import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { energies, greenShare, groupBy, total, unitPrice, SOLAR, UNCLASSIFIED, WIND } from "../mix";
import type { MixTotal } from "../mix";
import type { EnergyMix, MixDocument, MixRow } from "../types";

const GREY = "#9aa0a6";
const COLORS: Record<string, string> = { [SOLAR]: "#f2a900", [WIND]: "#1a9bb5", [UNCLASSIFIED]: "#9ccc65" };
const OTHER_COLORS = ["#2e7d32", "#7b61ff", "#d81b60"];
const colorOf = (energy: string, list: string[]) =>
  COLORS[energy] ?? OTHER_COLORS[list.filter((name) => !(name in COLORS)).indexOf(energy) % OTHER_COLORS.length];

const kwh = (value: number | null | undefined) => (value === null || value === undefined ? "—" : Math.round(value).toLocaleString());
const money = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `NT$${Math.round(value).toLocaleString()}`);
const percent = (value: number | null) => (value === null ? "—" : `${(value * 100).toFixed(1)}%`);
const price = (value: number | null) => (value === null ? "—" : value.toFixed(4));
const NO_SITE = "(no site)";
const SOURCES: Record<string, string> = { transfer_list: "Taipower list", statement: "Retailer", bill: "Bill 轉供度數" };

/** Grey and green power (solar, wind ...) per month and site. */
export function Dashboard({ onOpenSettings }: { onOpenSettings: () => void }) {
  const [mix, setMix] = useState<EnergyMix | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submittedOnly, setSubmittedOnly] = useState(false);
  const [year, setYear] = useState("all");
  const [site, setSite] = useState("all");

  const load = useCallback(() => {
    setError(null);
    api.dashboard(submittedOnly).then(setMix).catch((reason: Error) => setError(reason.message));
  }, [submittedOnly]);
  useEffect(load, [load]);

  const years = useMemo(() => [...new Set((mix?.rows ?? []).map((row) => row.month.slice(0, 4)))].sort(), [mix]);
  const sites = useMemo(() => [...new Set((mix?.rows ?? []).map((row) => row.site ?? NO_SITE))].sort(), [mix]);
  const rows = useMemo(
    () => (mix?.rows ?? []).filter((row) => (year === "all" || row.month.startsWith(year)) && (site === "all" || (row.site ?? NO_SITE) === site)),
    [mix, year, site],
  );
  const kinds = useMemo(() => energies(rows), [rows]);
  const sum = useMemo(() => total(rows), [rows]);
  const months = useMemo(() => groupBy(rows, (row) => row.month), [rows]);
  const bySite = useMemo(() => groupBy(rows, (row) => row.site ?? NO_SITE), [rows]);

  if (error) return <div className="dashboard"><p className="notice">Could not load the dashboard: {error}</p></div>;
  if (!mix) return <div className="dashboard"><p className="hint">Loading…</p></div>;

  return (
    <div className="dashboard">
      <div className="dash-head">
        <h2>Grey &amp; green power</h2>
        <label>Year <select value={year} onChange={(event) => setYear(event.target.value)}>
          <option value="all">All</option>
          {years.map((name) => <option key={name}>{name}</option>)}
        </select></label>
        <label>Site <select value={site} onChange={(event) => setSite(event.target.value)}>
          <option value="all">All sites</option>
          {sites.map((name) => <option key={name}>{name}</option>)}
        </select></label>
        <label className="check"><input type="checkbox" checked={submittedOnly} onChange={(event) => setSubmittedOnly(event.target.checked)} /> Submitted only</label>
        <button type="button" onClick={load}>Refresh</button>
      </div>

      <Warnings mix={mix} sum={sum} onOpenSettings={onOpenSettings} />

      {rows.length === 0 ? (
        <div className="welcome"><h2>Nothing to show yet</h2><p>Upload Taipower bills and green power documents (Taipower&apos;s transfer fee lists, retailers&apos; statements or purchase notices).</p></div>
      ) : (
        <>
          <div className="kpis">
            <Kpi label="Total usage" value={sum.with_bill ? `${kwh(sum.total_kwh)} kWh` : "—"} note={sum.with_bill ? "where the bill is in" : "no bills yet"} />
            <Kpi label="Grey power 灰電" value={sum.with_bill ? `${kwh(sum.grey_kwh)} kWh` : "—"} color={GREY} />
            {kinds.map((energy) => (
              <Kpi key={energy} label={`${energy}`} value={`${kwh(sum.green_kwh[energy])} kWh`} color={colorOf(energy, kinds)} />
            ))}
            <Kpi label="Green share 綠電占比" value={percent(greenShare(sum))} />
            <Kpi label="Grey cost (before tax)" value={sum.with_bill ? money(sum.grey_cost) : "—"} note={`${price(unitPrice(sum.grey_cost_priced, sum.grey_kwh_costed))} NT$/kWh`} />
            <Kpi label="Green cost (untaxed)" value={money(sum.green_cost)} note={`${price(unitPrice(sum.green_cost_priced, sum.green_kwh_costed))} NT$/kWh`} />
          </div>

          <section className="dash-card">
            <h3>kWh per month</h3>
            <MonthChart months={months} kinds={kinds} />
          </section>

          <section className="dash-card">
            <h3>By site</h3>
            <TotalsTable groups={bySite} overall={sum} kinds={kinds} first="Site" />
          </section>

          <section className="dash-card">
            <h3>By month</h3>
            <TotalsTable groups={months} overall={sum} kinds={kinds} first="Month" />
          </section>

          <details className="dash-card">
            <summary>Each meter and month ({rows.length})</summary>
            <MeterTable rows={rows} kinds={kinds} />
          </details>

          <details className="dash-card" open={mix.duplicates > 0}>
            <summary>Documents counted ({mix.documents.length - mix.duplicates}{mix.duplicates ? `, ${mix.duplicates} duplicate(s) left out` : ""})</summary>
            <DocumentTable documents={mix.documents} />
          </details>
        </>
      )}
    </div>
  );
}

function Warnings({ mix, sum, onOpenSettings }: { mix: EnergyMix; sum: MixTotal; onOpenSettings: () => void }) {
  const items: React.ReactNode[] = [];
  if (!mix.sites_set) items.push(<>No sites set: every meter on Taipower&apos;s transfer lists counts as yours. <button className="link" onClick={onOpenSettings}>Settings → Sites</button></>);
  if (mix.unclassified_services.length) {
    items.push(<>Energy not known for service {mix.unclassified_services.join(", ")}: shown as 未分類. <button className="link" onClick={onOpenSettings}>Settings → Green power services</button></>);
  }
  if (mix.duplicates) items.push(<>{mix.duplicates} document(s) uploaded more than once: counted once (see Documents counted below).</>);
  if (sum.without_bill) items.push(<>{sum.without_bill} meter-month(s) have green power but no bill yet: left out of total usage and the green share.</>);
  if (!items.length && !mix.notes.length) return null;
  return (
    <div className="dash-warnings">
      {items.map((item, index) => <p key={index} className="warn-text">⚠ {item}</p>)}
      {mix.notes.length > 0 && (
        <details><summary>{mix.notes.length} document(s) left out</summary><ul>{mix.notes.map((note) => <li key={note}>{note}</li>)}</ul></details>
      )}
    </div>
  );
}

function Kpi({ label, value, note, color }: { label: string; value: string; note?: string; color?: string }) {
  return (
    <div className="kpi">
      <span className="kpi-label">{color && <i style={{ background: color }} />}{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </div>
  );
}

/** Stacked bars: grey, then each energy; the green share above. */
function MonthChart({ months, kinds }: { months: [string, MixTotal][]; kinds: string[] }) {
  const width = 860, height = 260, left = 64, bottom = 28, top = 22;
  const highest = Math.max(1, ...months.map(([, sum]) => sum.grey_kwh + sum.green_total));
  const slot = (width - left) / Math.max(months.length, 1);
  const bar = Math.min(56, slot * 0.6);
  const y = (value: number) => top + (height - top - bottom) * (1 - value / highest);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((part) => highest * part);
  const layers = [{ name: "灰電", color: GREY, value: (sum: MixTotal) => sum.grey_kwh }, ...kinds.map((energy) => ({
    name: energy, color: colorOf(energy, kinds), value: (sum: MixTotal) => sum.green_kwh[energy] ?? 0,
  }))];
  return (
    <>
      <div className="legend">
        {layers.map((layer) => <span key={layer.name}><i style={{ background: layer.color }} />{layer.name}</span>)}
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="chart" role="img" aria-label="Grey and green kWh per month">
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={left} x2={width} y1={y(tick)} y2={y(tick)} className="grid" />
            <text x={left - 6} y={y(tick) + 4} textAnchor="end" className="axis">{tick >= 10000 ? `${Math.round(tick / 1000).toLocaleString()}k` : Math.round(tick)}</text>
          </g>
        ))}
        {months.map(([month, sum], index) => {
          const x = left + slot * index + (slot - bar) / 2;
          let base = 0;
          const share = greenShare(sum);
          return (
            <g key={month}>
              {layers.map((layer) => {
                const value = layer.value(sum);
                if (!value) return null;
                const rect = <rect key={layer.name} x={x} width={bar} y={y(base + value)} height={y(base) - y(base + value)} fill={layer.color}>
                  <title>{`${month} ${layer.name}: ${kwh(value)} kWh`}</title>
                </rect>;
                base += value;
                return rect;
              })}
              {share !== null && <text x={x + bar / 2} y={y(base) - 6} textAnchor="middle" className="share">{percent(share)}</text>}
              <text x={x + bar / 2} y={height - 8} textAnchor="middle" className="axis">{month}</text>
            </g>
          );
        })}
      </svg>
    </>
  );
}

function TotalsTable({ groups, overall, kinds, first }: { groups: [string, MixTotal][]; overall: MixTotal; kinds: string[]; first: string }) {
  const rows: [string, MixTotal][] = groups.length > 1 ? [...groups, ["Total", overall]] : groups;
  return (
    <div className="green-scroll">
      <table className="dash-table">
        <thead>
          <tr>
            <th>{first}</th><th>灰電 kWh</th>{kinds.map((energy) => <th key={energy}>{energy} kWh</th>)}
            <th>綠電 kWh</th><th>Total kWh</th><th>綠電占比</th><th>灰電費用</th><th>灰電 NT$/kWh</th><th>綠電費用</th><th>綠電 NT$/kWh</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, sum]) => (
            <tr key={name} className={name === "Total" ? "total" : ""}>
              <td>{name}</td>
              <td className="num">{sum.with_bill ? kwh(sum.grey_kwh) : "—"}</td>
              {kinds.map((energy) => <td key={energy} className="num">{kwh(sum.green_kwh[energy] ?? 0)}</td>)}
              <td className="num">{kwh(sum.green_total)}</td>
              <td className="num">{sum.total_kwh ? kwh(sum.total_kwh) : "—"}</td>
              <td className="num">{percent(greenShare(sum))}</td>
              <td className="num">{sum.with_bill ? money(sum.grey_cost) : "—"}</td>
              <td className="num">{price(unitPrice(sum.grey_cost_priced, sum.grey_kwh_costed))}</td>
              <td className="num">{money(sum.green_cost)}</td>
              <td className="num">{price(unitPrice(sum.green_cost_priced, sum.green_kwh_costed))}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const TYPE_NAMES: Record<string, string> = { bill: "Bill / other", statement: "售電明細", notice: "購電通知單", transfer_list: "台電清單" };

/** Every document with what it adds, so the totals can be traced back. */
function DocumentTable({ documents }: { documents: MixDocument[] }) {
  return (
    <div className="green-scroll">
      <table className="dash-table">
        <thead>
          <tr><th>File</th><th>Type</th><th>Period</th><th>Service / 電號</th><th>kWh</th><th>Cost</th><th>of which 餘電</th><th>Counted</th><th>Uploaded by</th></tr>
        </thead>
        <tbody>
          {documents.map((doc) => (
            <tr key={doc.id} className={doc.duplicate_of ? "duplicate" : ""}>
              <td title={`#${doc.id}`}>{doc.filename}{doc.page ? ` p.${doc.page}` : ""}</td>
              <td>{TYPE_NAMES[doc.type] ?? doc.type}</td>
              <td>{doc.period ?? "—"}</td>
              <td>{doc.service ?? "—"}</td>
              <td className="num">{kwh(doc.kwh)}</td>
              <td className="num">{money(doc.cost)}</td>
              <td className="num">{doc.surcharge ? money(doc.surcharge) : ""}</td>
              <td>{doc.duplicate_of ? `No — same as ${doc.duplicate_of}` : doc.counted === "kwh" ? "kWh (fees are in the retailer's cost)" : doc.counted === "cost" ? "Cost" : "kWh and cost"}</td>
              <td>{doc.user}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MeterTable({ rows, kinds }: { rows: MixRow[]; kinds: string[] }) {
  return (
    <div className="green-scroll">
      <table className="dash-table">
        <thead>
          <tr>
            <th>Month</th><th>Site</th><th>電號</th><th>灰電 kWh</th>{kinds.map((energy) => <th key={energy}>{energy} kWh</th>)}
            <th>綠電占比</th><th>灰電費用</th><th>綠電費用</th><th>Green kWh from</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.month}-${row.meter ?? row.document}`}>
              <td>{row.month}</td>
              <td>{row.site ?? "—"}</td>
              <td>{row.meter ?? row.document ?? "—"}</td>
              <td className="num">{row.grey_kwh === null ? "no bill" : kwh(row.grey_kwh)}</td>
              {kinds.map((energy) => <td key={energy} className="num">{row.green_kwh[energy] ? kwh(row.green_kwh[energy]) : ""}</td>)}
              <td className="num">{percent(row.green_share)}</td>
              <td className="num">{money(row.grey_cost)}</td>
              <td className="num">{money(row.green_cost)}</td>
              <td>{row.green_source ? SOURCES[row.green_source] : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
