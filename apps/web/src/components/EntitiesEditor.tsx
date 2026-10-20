import { useEffect, useState } from "react";
import { api } from "../api";
import type { Entity } from "../types";

interface Draft {
  id?: number;
  name: string;
  code: string;
  tax_id: string;
  note: string;
}

const toDraft = (entity: Entity): Draft => ({
  id: entity.id,
  name: entity.name,
  code: entity.code ?? "",
  tax_id: entity.tax_id ?? "",
  note: entity.note ?? "",
});

/** The group's companies: each has its sites, and its own retailers' notices. */
export function EntitiesEditor({ canEdit, onSaved }: { canEdit: boolean; onSaved: (entities: Entity[]) => void }) {
  const [saved, setSaved] = useState<Entity[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = (entities: Entity[]) => {
    setSaved(entities);
    setDrafts(entities.map(toDraft));
    onSaved(entities);
  };
  useEffect(() => {
    api.entities().then(load).catch((error: Error) => setMessage({ kind: "error", text: error.message }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const dirty = JSON.stringify(drafts) !== JSON.stringify(saved.map(toDraft));
  const update = (index: number, patch: Partial<Draft>) =>
    setDrafts((current) => current.map((draft, at) => (at === index ? { ...draft, ...patch } : draft)));

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      load(await api.saveEntities(drafts.filter((draft) => draft.name.trim())));
      setMessage({ kind: "ok", text: "Entities saved. Choose each site's entity below." });
    } catch (error) {
      setMessage({ kind: "error", text: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="sites-editor">
      {drafts.length === 0 && <p className="hint">No entities yet. Without them, every notice for a service counts as the company&apos;s.</p>}
      {drafts.map((draft, index) => (
        <div key={draft.id ?? `new-${index}`} className="site-row entity-row">
          <label>
            <span>Entity</span>
            <input value={draft.name} disabled={!canEdit || busy} placeholder="e.g. ASML TW21" onChange={(event) => update(index, { name: event.target.value })} />
          </label>
          <label>
            <span>Code (on file names)</span>
            <input value={draft.code} disabled={!canEdit || busy} placeholder="e.g. TW21" onChange={(event) => update(index, { code: event.target.value })} />
          </label>
          <label>
            <span>統一編號</span>
            <input value={draft.tax_id} disabled={!canEdit || busy} placeholder="e.g. 12345678" onChange={(event) => update(index, { tax_id: event.target.value })} />
          </label>
          <label className="note">
            <span>Note</span>
            <input value={draft.note} disabled={!canEdit || busy} onChange={(event) => update(index, { note: event.target.value })} />
          </label>
          {canEdit && (
            <button type="button" className="icon" aria-label={`Remove ${draft.name || "entity"}`} title="Remove this entity" disabled={busy}
              onClick={() => setDrafts((current) => current.filter((_, at) => at !== index))}>
              ✕
            </button>
          )}
        </div>
      ))}
      {message && <p className={`message ${message.kind}`}>{message.text}</p>}
      {canEdit && (
        <div className="sites-actions">
          <button type="button" disabled={busy} onClick={() => setDrafts((current) => [...current, { name: "", code: "", tax_id: "", note: "" }])}>
            + Entity
          </button>
          <button type="button" disabled={busy || !dirty} onClick={() => setDrafts(saved.map(toDraft))}>Undo</button>
          <button type="button" className="primary" disabled={busy || !dirty} onClick={save}>Save entities</button>
        </div>
      )}
    </div>
  );
}
