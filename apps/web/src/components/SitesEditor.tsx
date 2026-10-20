import { useEffect, useState } from "react";
import { api } from "../api";
import type { Entity, Site } from "../types";

interface Draft {
  id?: number;
  name: string;
  code: string;
  note: string;
  entity_id: number | null;
  /** 電號, one per line while editing. */
  meters: string;
}

const toDraft = (site: Site): Draft => ({
  id: site.id,
  name: site.name,
  code: site.code ?? "",
  note: site.note ?? "",
  entity_id: site.entity_id ?? null,
  meters: site.meters.join("\n"),
});

/** The company's sites and the meters (電號) each one has; bills are placed by their 電號. */
export function SitesEditor({ canEdit, entities }: { canEdit: boolean; entities: Entity[] }) {
  const [saved, setSaved] = useState<Site[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "error"; text: string } | null>(null);

  const load = (sites: Site[]) => {
    setSaved(sites);
    setDrafts(sites.map(toDraft));
  };
  useEffect(() => {
    api.sites().then(load).catch((error: Error) => setMessage({ kind: "error", text: error.message }));
  }, []);

  const dirty = JSON.stringify(drafts) !== JSON.stringify(saved.map(toDraft));
  const update = (index: number, patch: Partial<Draft>) =>
    setDrafts((current) => current.map((draft, at) => (at === index ? { ...draft, ...patch } : draft)));

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const sites = drafts
        .filter((draft) => draft.name.trim() || draft.meters.trim())
        .map((draft) => ({ id: draft.id, name: draft.name, code: draft.code, note: draft.note, entity_id: draft.entity_id, meters: draft.meters.split(/[\n,，、]/) }));
      load(await api.saveSites(sites));
      setMessage({ kind: "ok", text: "Sites saved. Bills are placed by their 電號 (a bill's site can still be chosen by hand)." });
    } catch (error) {
      setMessage({ kind: "error", text: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="sites-editor">
      {drafts.length === 0 && <p className="hint">No sites yet. Add one for each place with its own electricity (a building, a floor, a plant).</p>}
      {drafts.map((draft, index) => (
        <div key={draft.id ?? `new-${index}`} className="site-row">
          <label>
            <span>Site</span>
            <input value={draft.name} disabled={!canEdit || busy} placeholder="e.g. 台北總部" onChange={(event) => update(index, { name: event.target.value })} />
          </label>
          <label>
            <span>Entity</span>
            <select value={draft.entity_id ?? ""} disabled={!canEdit || busy}
              onChange={(event) => update(index, { entity_id: event.target.value ? Number(event.target.value) : null })}>
              <option value="">—</option>
              {entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name}</option>)}
            </select>
          </label>
          <label>
            <span>Code</span>
            <input value={draft.code} disabled={!canEdit || busy} placeholder="e.g. Intelex site code" onChange={(event) => update(index, { code: event.target.value })} />
          </label>
          <label className="meters">
            <span>電號 (one per line)</span>
            <textarea rows={2} value={draft.meters} disabled={!canEdit || busy} placeholder="00-12-3456-78-9" onChange={(event) => update(index, { meters: event.target.value })} />
          </label>
          <label className="note">
            <span>Note</span>
            <input value={draft.note} disabled={!canEdit || busy} onChange={(event) => update(index, { note: event.target.value })} />
          </label>
          {canEdit && (
            <button type="button" className="icon" aria-label={`Remove ${draft.name || "site"}`} title="Remove this site" disabled={busy}
              onClick={() => setDrafts((current) => current.filter((_, at) => at !== index))}>
              ✕
            </button>
          )}
        </div>
      ))}
      {message && <p className={`message ${message.kind}`}>{message.text}</p>}
      {canEdit && (
        <div className="sites-actions">
          <button type="button" disabled={busy} onClick={() => setDrafts((current) => [...current, { name: "", code: "", note: "", entity_id: null, meters: "" }])}>
            + Site
          </button>
          <button type="button" disabled={busy || !dirty} onClick={() => setDrafts(saved.map(toDraft))}>Undo</button>
          <button type="button" className="primary" disabled={busy || !dirty} onClick={save}>Save sites</button>
        </div>
      )}
    </div>
  );
}
