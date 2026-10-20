import { useEffect, useState } from "react";
import { api } from "../api";
import { EntitiesEditor } from "./EntitiesEditor";
import { SitesEditor } from "./SitesEditor";
import type { Entity, KeywordSetting, SettingsValues, SettingsView } from "../types";

const ENV_NAMES: Record<keyof SettingsValues, string> = {
  output_dir: "TAIPOWER_OUTPUT_DIR",
  workbooks: "TAIPOWER_WORKBOOKS",
  ocr_backend: "TAIPOWER_OCR_BACKEND",
  workbook_retry_seconds: "TAIPOWER_WORKBOOK_RETRY_SECONDS",
  host: "TAIPOWER_HOST",
  port: "TAIPOWER_PORT",
  green_tolerance: "TAIPOWER_GREEN_TOLERANCE",
  green_services: "TAIPOWER_GREEN_SERVICES",
  highlight_color: "TAIPOWER_HIGHLIGHT_COLOR",
  label_color: "TAIPOWER_LABEL_COLOR",
  company_names: "TAIPOWER_COMPANY_NAMES",
  self_terms: "TAIPOWER_SELF_TERMS",
  period_keywords: "TAIPOWER_PERIOD_KEYWORDS",
  usage_keywords: "TAIPOWER_USAGE_KEYWORDS",
  cost_keywords: "TAIPOWER_COST_KEYWORDS",
  exclude_keywords: "TAIPOWER_EXCLUDE_KEYWORDS",
};

// Keywords for documents that are not Taipower bills, in the order shown.
const KEYWORDS: { key: KeywordSetting; label: string; hint: string }[] = [
  { key: "company_names", label: "Company names", hint: "How landlords write your company: the row or paragraph naming it is preferred." },
  { key: "self_terms", label: "Words for your own share", hint: "e.g. 本公司, 貴公司, 承租戶 — also preferred." },
  { key: "period_keywords", label: "Billing period", hint: "Words before the period's dates." },
  { key: "usage_keywords", label: "Usage (kWh)", hint: "Words or units beside the kWh, most specific first." },
  { key: "cost_keywords", label: "Cost", hint: "Words beside the amount; 未稅/含稅 nearby tell before and after tax apart." },
  { key: "exclude_keywords", label: "Avoid values near", hint: "e.g. 全棟, 公設, 上期 — the building's or another period's numbers." },
];

const ENGINES: Record<string, string> = {
  crosscheck: "PaddleOCR + Windows OCR cross-check (recommended)",
  paddleocr: "PaddleOCR only",
  windows: "Windows OCR only (fastest, less accurate)",
};

const HOSTS: Record<string, string> = {
  "127.0.0.1": "This PC only",
  "0.0.0.0": "Other computers on the network too",
};

interface Props {
  onClose: () => void;
  /** After a save, so the page can show the new output folder. */
  onSaved: () => void;
}

export function SettingsDialog({ onClose, onSaved }: Props) {
  const [view, setView] = useState<SettingsView | null>(null);
  const [values, setValues] = useState<SettingsValues | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "warn" | "error"; text: string } | null>(null);
  const [entities, setEntities] = useState<Entity[]>([]);

  useEffect(() => {
    api.settings()
      .then((loaded) => { setView(loaded); setValues(loaded.values); })
      .catch((error: Error) => setMessage({ kind: "error", text: error.message }));
  }, []);

  if (!view || !values) {
    return (
      <div className="modal" onClick={onClose}>
        <div className="modal-body settings" onClick={(event) => event.stopPropagation()}>
          <p>{message ? message.text : "Loading…"}</p>
        </div>
      </div>
    );
  }

  const disabled = (key: keyof SettingsValues) => !view.can_edit || view.locked.includes(key) || busy;
  const lockNote = (key: keyof SettingsValues) =>
    view.locked.includes(key) ? <span className="hint">Set by the {ENV_NAMES[key]} environment variable</span> : null;
  const set = <K extends keyof SettingsValues>(key: K, value: SettingsValues[K]) => setValues({ ...values, [key]: value });
  const dirty = (Object.keys(values) as (keyof SettingsValues)[]).some((key) => JSON.stringify(values[key]) !== JSON.stringify(view.values[key]));

  const browse = async () => {
    setBusy(true);
    try {
      const { folder } = await api.browseFolder();
      if (folder) set("output_dir", folder);
    } catch (error) {
      setMessage({ kind: "error", text: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    setBusy(true);
    setMessage(null);
    try {
      const result = await api.saveSettings(values);
      setView(result);
      setValues(result.values);
      onSaved();
      const notes = [result.changed.length ? "Saved." : "Nothing changed."];
      if (result.restart) notes.push("Restart the app to use the new network address.");
      if (result.locked_workbooks.length) notes.push(`${result.locked_workbooks.join(" and ")} is open in Excel; it is written once closed.`);
      setMessage({ kind: result.restart || result.locked_workbooks.length ? "warn" : "ok", text: notes.join(" ") });
    } catch (error) {
      setMessage({ kind: "error", text: (error as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const running = view.running;
  const pendingRestart = running && (values.host !== running.host || values.port !== running.port);

  return (
    <div className="modal" onClick={onClose}>
      <div className="modal-body settings" role="dialog" aria-label="Settings" onClick={(event) => event.stopPropagation()}>
        <div className="modal-header">
          <h3>Settings</h3>
          <button onClick={onClose}>Close</button>
        </div>
        {!view.can_edit && (
          <p className="message warn">Settings can only be changed on the computer running this app.</p>
        )}

        <section>
          <h4>Results</h4>
          <label className="setting">
            <span>Output folder</span>
            <span className="row">
              <input value={values.output_dir} disabled={disabled("output_dir")} onChange={(event) => set("output_dir", event.target.value)} />
              {view.can_edit && !view.locked.includes("output_dir") && (
                <button type="button" onClick={browse} disabled={busy}>Browse…</button>
              )}
            </span>
            {lockNote("output_dir") ?? (
              <span className="hint">Excel files and evidence PDFs, e.g. a OneDrive-synced SharePoint folder. Bills already submitted keep their files where they are.</span>
            )}
          </label>
          <fieldset className="setting" disabled={disabled("workbooks")}>
            <span>Excel files</span>
            <label className="choice">
              <input type="radio" checked={values.workbooks === "shared"} onChange={() => set("workbooks", "shared")} />
              One shared pair for everyone (when this app runs as the server)
            </label>
            <label className="choice">
              <input type="radio" checked={values.workbooks === "per_user"} onChange={() => set("workbooks", "per_user")} />
              One pair per person, in <code>{"<output folder>\\<name>\\"}</code> (when each PC runs its own copy)
            </label>
            {lockNote("workbooks")}
          </fieldset>
          <label className="setting">
            <span>Retry a workbook open in Excel every</span>
            <span className="row">
              <input
                type="number"
                min={1}
                max={3600}
                className="short"
                value={values.workbook_retry_seconds}
                disabled={disabled("workbook_retry_seconds")}
                onChange={(event) => set("workbook_retry_seconds", Number(event.target.value))}
              />
              seconds
            </span>
            {lockNote("workbook_retry_seconds")}
          </label>
        </section>

        <section>
          <h4>Evidence marks</h4>
          <div className="setting">
            <span>Default colours</span>
            <div className="color-row">
              <label>
                <input
                  type="color"
                  value={values.highlight_color}
                  disabled={disabled("highlight_color")}
                  onChange={(event) => set("highlight_color", event.target.value)}
                />
                Highlighter
              </label>
              <label>
                <input
                  type="color"
                  value={values.label_color}
                  disabled={disabled("label_color")}
                  onChange={(event) => set("label_color", event.target.value)}
                />
                Text
              </label>
              {view.can_edit && (
                <button
                  type="button"
                  className="link"
                  disabled={busy || (values.highlight_color === "#ffeb3b" && values.label_color === "#d32f2f")}
                  onClick={() => setValues({ ...values, highlight_color: "#ffeb3b", label_color: "#d32f2f" })}
                >
                  Yellow and red
                </button>
              )}
            </div>
            {lockNote("highlight_color")}
            {lockNote("label_color")}
            <span className="hint">
              For marks without a colour of their own; each mark can be given its own colour in the marks list. Evidence PDFs already submitted keep their colours.
            </span>
          </div>
        </section>

        <section>
          <h4>Other documents (not Taipower bills)</h4>
          <span className="hint">
            Uploads that are not Taipower bills — such as a landlord&apos;s allocation for a shared meter — are read for these words.
            One per line. Changes apply to new uploads, or press &quot;Detect again&quot; on a document.
          </span>
          <div className="keyword-grid">
            {KEYWORDS.map(({ key, label, hint }) => (
              <label key={key} className="setting">
                <span>{label}</span>
                <textarea
                  rows={key === "company_names" ? 3 : 4}
                  value={values[key].join("\n")}
                  disabled={disabled(key)}
                  placeholder={key === "company_names" ? "e.g. 甲股份有限公司" : ""}
                  onChange={(event) => set(key, event.target.value.split("\n"))}
                />
                {lockNote(key) ?? <span className="hint">{hint}</span>}
              </label>
            ))}
          </div>
          {view.can_edit && (
            <button
              type="button"
              className="link"
              disabled={busy}
              onClick={() => setValues({ ...values, ...Object.fromEntries(KEYWORDS.filter(({ key }) => key !== "company_names").map(({ key }) => [key, view.keyword_defaults[key]])) })}
            >
              Default keywords (company names are kept)
            </button>
          )}
        </section>

        <section>
          <h4>Green power statements</h4>
          <label className="setting">
            <span>Allowed difference per line</span>
            <span className="row">
              <input
                type="number"
                min={0}
                step={1}
                className="short"
                value={values.green_tolerance}
                disabled={disabled("green_tolerance")}
                onChange={(event) => set("green_tolerance", Number(event.target.value))}
              />
              NT$
            </span>
            {lockNote("green_tolerance") ?? (
              <span className="hint">
                How far a statement line&apos;s charge may be from kWh × (base price + transfer rate + certificate rate) before it is flagged.
                Retailers round finer amounts one by one, so a few NT$ is normal.
              </span>
            )}
          </label>
          <label className="setting">
            <span>Green power services 服務編號 → 能源別</span>
            <textarea
              rows={3}
              value={values.green_services.join("\n")}
              disabled={disabled("green_services")}
              placeholder={"1-11210-08-00210 太陽能\n1-11311-08-00502 風力"}
              onChange={(event) => set("green_services", event.target.value.split("\n"))}
            />
            {lockNote("green_services") ?? (
              <span className="hint">
                One per line: a transfer service number (on Taipower&apos;s transfer fee list and the purchase notice), then its energy.
                The dashboard splits green power by energy with it; a retailer&apos;s statement already says which.
              </span>
            )}
          </label>
        </section>

        <section>
          <h4>Entities</h4>
          <span className="hint">
            The group&apos;s companies. Each has its sites, and its own retailers&apos; purchase notices: a notice counts only for its
            company&apos;s meters (kWh on Taipower&apos;s list × the notice&apos;s price). A notice is matched by the code or 統一編號 in its
            file name or text, or chosen on the review page.
          </span>
          <EntitiesEditor canEdit={view.can_edit} onSaved={setEntities} />
        </section>

        <section>
          <h4>Sites</h4>
          <span className="hint">
            Each site and its meters (電號). A bill is placed in the site its 電號 belongs to (00-12-3456-78-9 and 00123456789 are the same);
            documents without one are placed by hand on the review page. The Excel files&apos; Monthly sheet lists each bill per site and month.
          </span>
          <SitesEditor canEdit={view.can_edit} entities={entities} />
        </section>

        <section>
          <h4>OCR</h4>
          <label className="setting">
            <span>Engine</span>
            <select value={values.ocr_backend} disabled={disabled("ocr_backend")} onChange={(event) => set("ocr_backend", event.target.value)}>
              {view.options.ocr_backend.map((name) => <option key={name} value={name}>{ENGINES[name] ?? name}</option>)}
            </select>
            {lockNote("ocr_backend") ?? <span className="hint">Used for uploads from now on. PDF pages are read at {view.dpi} DPI.</span>}
          </label>
        </section>

        <section>
          <h4>Network</h4>
          <label className="setting">
            <span>Who can open this app</span>
            <select value={values.host} disabled={disabled("host")} onChange={(event) => set("host", event.target.value)}>
              {[...new Set([...Object.keys(HOSTS), values.host])].map((host) => (
                <option key={host} value={host}>{HOSTS[host] ? `${HOSTS[host]} (${host})` : host}</option>
              ))}
            </select>
            {lockNote("host")}
            {values.host !== "127.0.0.1" && (
              <span className="hint danger">There is no login: anyone on the network can open, edit and submit bills.</span>
            )}
          </label>
          <label className="setting">
            <span>Port</span>
            <input
              type="number"
              min={1024}
              max={65535}
              className="short"
              value={values.port}
              disabled={disabled("port")}
              onChange={(event) => set("port", Number(event.target.value))}
            />
            {lockNote("port")}
          </label>
          <span className="hint">
            {running ? `Running on ${running.host}:${running.port}. ` : ""}
            {pendingRestart ? "Restart the app to use the new address." : "Network changes take effect after restarting the app."}
          </span>
        </section>

        {message && <p className={`message ${message.kind}`}>{message.text}</p>}
        {view.can_edit && (
          <div className="modal-footer">
            <button onClick={() => setValues(view.values)} disabled={busy || !dirty}>Undo changes</button>
            <button className="primary" onClick={save} disabled={busy || !dirty}>Save</button>
          </div>
        )}
      </div>
    </div>
  );
}
