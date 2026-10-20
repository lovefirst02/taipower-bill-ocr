import type { CostBreakdown, CostItem } from "../types";

const money = (value: number | null | undefined) => (value === null || value === undefined ? "—" : `NT$${Math.round(value).toLocaleString()}`);
const rate = (value: number | null | undefined) => (value === null || value === undefined ? "—" : value.toFixed(4));

/** ✓ / ⚠ and what the item was held against. */
function verdict(item: CostItem): { mark: string; className: string; text: string } {
  const check = item.check;
  if (!check) return { mark: "", className: "", text: "" };
  if (item.key === "surplus" && check.peers?.length) {
    const others = check.peers.map((peer) => `${peer.entity}${peer.self ? " (this)" : ""} ${peer.per_kwh.toFixed(4)}`).join(" · ");
    return check.status === "check"
      ? { mark: "⚠", className: "check", text: `Shared differently from the other companies: ${others}` }
      : { mark: "✓", className: "ok", text: `Same per kWh for every company: ${others}` };
  }
  if (check.status === "none") return { mark: "", className: "muted", text: check.note ?? "" };
  if (item.key === "contract") {
    return check.status === "ok"
      ? { mark: "✓", className: "ok", text: `= contract ${rate(check.reference)}` }
      : { mark: "⚠", className: "check", text: check.note ?? "not the contract price" };
  }
  if (item.key === "taipower") {
    const against = `Taipower's fees ${money(check.reference)} (${rate(check.reference_per_kwh)}/kWh${check.sections?.length ? `; ${check.sections.join("、")}` : ""})`;
    if (check.status === "ok") return { mark: "✓", className: "ok", text: `= ${against}` };
    const label = check.status === "over" ? "charged more than" : "differs from";
    return { mark: "⚠", className: "check", text: `${(check.difference ?? 0) > 0 ? "+" : ""}${money(check.difference)} — ${label} ${against}` };
  }
  return { mark: check.status === "ok" ? "✓" : "⚠", className: check.status === "ok" ? "ok" : "check", text: check.note ?? "" };
}

/** One document's cost per kWh, item by item, each with its check. */
export function CostBreakdownTable({ cost }: { cost: CostBreakdown }) {
  const items = cost.items.filter((item) => item.amount !== null && (item.amount !== 0 || item.check));
  return (
    <table className="dash-table cost-table">
      <thead>
        <tr><th>Item</th><th>NT$</th><th>per kWh</th><th>Check</th></tr>
      </thead>
      <tbody>
        {items.map((item) => {
          const { mark, className, text } = verdict(item);
          return (
            <tr key={item.key}>
              <td>{item.label}</td>
              <td className="num">{money(item.amount)}</td>
              <td className="num">{rate(item.per_kwh)}</td>
              <td className={`verdict ${className}`}>{mark} {text}</td>
            </tr>
          );
        })}
        <tr className="total">
          <td>{cost.total.label}</td>
          <td className="num">{money(cost.total.amount)}</td>
          <td className="num">{rate(cost.total.per_kwh)}</td>
          <td className="verdict muted">{cost.kwh.toLocaleString()} kWh</td>
        </tr>
      </tbody>
    </table>
  );
}

const COLUMNS: { key: CostItem["key"]; label: string }[] = [
  { key: "contract", label: "合約電價" },
  { key: "taipower", label: "台電轉供" },
  { key: "certificate", label: "憑證" },
  { key: "surplus", label: "餘電" },
  { key: "fee", label: "手續費" },
];

/** Every document's cost per kWh side by side; ⚠ where an item is not what it should be. */
export function CostSummary({ costs, onOpen }: { costs: CostBreakdown[]; onOpen: (id: number) => void }) {
  return (
    <div className="green-scroll">
      <table className="dash-table cost-summary">
        <thead>
          <tr>
            <th>Month</th><th>Energy</th><th>Entity</th><th>Document</th><th>kWh</th>
            {COLUMNS.map((column) => <th key={column.key}>{column.label} /kWh</th>)}
            <th>合計 /kWh</th>
          </tr>
        </thead>
        <tbody>
          {costs.map((cost) => (
            <tr key={cost.id}>
              <td>{cost.month}</td>
              <td>{cost.energy ?? "—"}</td>
              <td>{cost.entity ?? "—"}</td>
              <td><button type="button" className="link" onClick={() => onOpen(cost.id)}>{cost.filename}</button></td>
              <td className="num">{cost.kwh.toLocaleString()}</td>
              {COLUMNS.map((column) => {
                const item = cost.items.find((candidate) => candidate.key === column.key);
                if (!item || item.amount === null || (item.amount === 0 && !item.check)) return <td key={column.key} className="num">—</td>;
                const { mark, className, text } = verdict(item);
                return (
                  <td key={column.key} className={`num verdict ${className}`} title={text}>
                    {rate(item.per_kwh)} {mark}
                  </td>
                );
              })}
              <td className="num"><strong>{rate(cost.total.per_kwh)}</strong></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
