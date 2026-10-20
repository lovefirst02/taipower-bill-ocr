import { useEffect, useState } from "react";
import { api } from "../api";
import type { Contract, Entity } from "../types";

interface Draft {
  entity_id: number | null;
  energy: string;
  retailer: string;
  price: string;
  transfer_rate: string;
  certificate_rate: string;
  start_month: string;
  end_month: string;
  note: string;
}

const toDraft = (contract: Contract): Draft => ({
  entity_id: contract.entity_id,
  energy: contract.energy,
  retailer: contract.retailer ?? "",
  price: String(contract.price),
  transfer_rate: String(contract.transfer_rate ?? 0),
  certificate_rate: String(contract.certificate_rate ?? 0),
  start_month: contract.start_month ?? "",
  end_month: contract.end_month ?? "",
  note: contract.note ?? "",
});

const EMPTY: Draft = { entity_id: null, energy: "太陽能", retailer: "", price: "", transfer_rate: "0", certificate_rate: "0", start_month: "", end_month: "", note: "" };

/** Green power contracts: each energy's price, used to estimate budgets left without a green cost. */
export function ContractsEditor({ canEdit, entities }: { canEdit: boolean; entities: Entity[] }) {
  const [saved, setSaved] = useState<Contract[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = (contracts: Contract[]) => {
    setSaved(contracts);
    setDrafts(contracts.map(toDraft));
  };
  useEffect(() => {
    api.contracts().then(load).catch((error: Error) => setMessage({ kind: "error", text: error.message }));
  }, []);

  const dirty = JSON.stringify(drafts) !== JSON.stringify(saved.map(toDraft));
  const update = (index: number, patch: Partial<Draft>) =>
    setDrafts((current) => current.map((draft, at) => (at === index ? { ...draft, ...patch } : draft)));
  const number = (text: string) => Number(text.replace(/[,\s]/g, "") || 0);

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      load(await api.saveContracts(drafts.filter((draft) => draft.price.trim()).map((draft) => ({
        entity_id: draft.entity_id, energy: draft.energy, retailer: draft.retailer || null, service: null,
        price: number(draft.price), transfer_rate: number(draft.transfer_rate), certificate_rate: number(draft.certificate_rate),
        start_month: draft.start_month || null, end_month: draft.end_month || null, note: draft.note || null,
      }))));
      setMessage({ kind: "ok", text: "Contracts saved. Budgets without a green cost are estimated with them." });
    } catch (error) {
      setMessage({ kind: "error", text: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="sites-editor">
      {drafts.length === 0 && <p className="hint">No contracts yet.</p>}
      {drafts.map((draft, index) => {
        const perKwh = number(draft.price) + number(draft.transfer_rate) + number(draft.certificate_rate);
        return (
          <div key={index} className="site-row contract-row">
            <label>
              <span>Entity</span>
              <select value={draft.entity_id ?? ""} disabled={!canEdit || busy}
                onChange={(event) => update(index, { entity_id: event.target.value ? Number(event.target.value) : null })}>
                <option value="">All entities</option>
                {entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name}</option>)}
              </select>
            </label>
            <label>
              <span>Energy</span>
              <select value={draft.energy} disabled={!canEdit || busy} onChange={(event) => update(index, { energy: event.target.value })}>
                <option>太陽能</option>
                <option>風力</option>
              </select>
            </label>
            <label>
              <span>Retailer</span>
              <input value={draft.retailer} disabled={!canEdit || busy} placeholder="e.g. 辰華電力" onChange={(event) => update(index, { retailer: event.target.value })} />
            </label>
            <label>
              <span>Price NT$/kWh</span>
              <input value={draft.price} disabled={!canEdit || busy} inputMode="decimal" placeholder="5.6" onChange={(event) => update(index, { price: event.target.value })} />
            </label>
            <label title="Taipower's transfer fees charged on top of the price; 0 when the price includes them">
              <span>+ Transfer NT$/kWh</span>
              <input value={draft.transfer_rate} disabled={!canEdit || busy} inputMode="decimal" onChange={(event) => update(index, { transfer_rate: event.target.value })} />
            </label>
            <label title="Certificate fees charged on top of the price">
              <span>+ Certificate</span>
              <input value={draft.certificate_rate} disabled={!canEdit || busy} inputMode="decimal" onChange={(event) => update(index, { certificate_rate: event.target.value })} />
            </label>
            <label>
              <span>From (YYYY-MM)</span>
              <input value={draft.start_month} disabled={!canEdit || busy} placeholder="2026-01" onChange={(event) => update(index, { start_month: event.target.value })} />
            </label>
            <label>
              <span>To</span>
              <input value={draft.end_month} disabled={!canEdit || busy} placeholder="open" onChange={(event) => update(index, { end_month: event.target.value })} />
            </label>
            <span className="per-kwh" title="What a kWh costs under this contract">= {perKwh ? perKwh.toFixed(4) : "—"}</span>
            {canEdit && (
              <button type="button" className="icon" aria-label="Remove contract" disabled={busy}
                onClick={() => setDrafts((current) => current.filter((_, at) => at !== index))}>✕</button>
            )}
          </div>
        );
      })}
      {message && <p className={`message ${message.kind}`}>{message.text}</p>}
      {canEdit && (
        <div className="sites-actions">
          <button type="button" disabled={busy} onClick={() => setDrafts((current) => [...current, { ...EMPTY }])}>+ Contract</button>
          <button type="button" disabled={busy || !dirty} onClick={() => setDrafts(saved.map(toDraft))}>Undo</button>
          <button type="button" className="primary" disabled={busy || !dirty} onClick={save}>Save contracts</button>
        </div>
      )}
    </div>
  );
}
