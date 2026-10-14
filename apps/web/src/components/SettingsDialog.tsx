import { useEffect, useState } from "react";
import { api } from "../api";
import type { SettingsValues, SettingsView } from "../types";

const ENV_NAMES: Record<keyof SettingsValues, string> = {
  output_dir: "TAIPOWER_OUTPUT_DIR",
  workbooks: "TAIPOWER_WORKBOOKS",
  ocr_backend: "TAIPOWER_OCR_BACKEND",
  workbook_retry_seconds: "TAIPOWER_WORKBOOK_RETRY_SECONDS",
  host: "TAIPOWER_HOST",
  port: "TAIPOWER_PORT",
};

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
  const dirty = (Object.keys(values) as (keyof SettingsValues)[]).some((key) => values[key] !== view.values[key]);

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
