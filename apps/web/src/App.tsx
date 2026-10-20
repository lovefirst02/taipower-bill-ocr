import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Dashboard } from "./components/Dashboard";
import { Review } from "./components/Review";
import { SettingsDialog } from "./components/SettingsDialog";
import { UploadDialog } from "./components/UploadDialog";
import type { UploadTarget } from "./components/UploadDialog";
import { BillList, Dropzone, UploadQueue } from "./components/Sidebar";
import type { Column, ItemSummary, MarkColors, OutputInfo, Site, Upload } from "./types";

const USER_KEY = "taipower-user";

function readUser(): string {
  try {
    return localStorage.getItem(USER_KEY) ?? "";
  } catch {
    return "";
  }
}

function UserGate({ initial, onDone }: { initial: string; onDone: (name: string) => void }) {
  const [name, setName] = useState(initial);
  return (
    <div className="modal">
      <form
        className="modal-body user-gate"
        onSubmit={(event) => {
          event.preventDefault();
          if (name.trim()) onDone(name.trim());
        }}
      >
        <h2>Who is reviewing?</h2>
        <p>Your name is saved with your uploads so you can export your own bills.</p>
        <input autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="Name or email" />
        <button className="primary" type="submit" disabled={!name.trim()}>Continue</button>
      </form>
    </div>
  );
}

export default function App() {
  const [user, setUser] = useState(readUser);
  const [editingUser, setEditingUser] = useState(false);
  const [columns, setColumns] = useState<Column[]>([]);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [items, setItems] = useState<ItemSummary[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [uploading, setUploading] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [output, setOutput] = useState<OutputInfo | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showDashboard, setShowDashboard] = useState(false);
  // The upload dialog: the files dropped to open it, and the last choice of what they are.
  const [uploadFiles, setUploadFiles] = useState<File[] | null>(null);
  const [uploadTarget, setUploadTarget] = useState<UploadTarget>(null);
  const [sites, setSites] = useState<Site[]>([]);
  const openUpload = (files: File[]) => {
    api.sites().then(setSites).catch(() => undefined);
    setUploadFiles(files);
  };
  const [settingsVersion, setSettingsVersion] = useState(0);
  const [colors, setColors] = useState<MarkColors | null>(null);
  const loadColors = useCallback(() => {
    api.settings()
      .then((view) => setColors({ highlight: view.values.highlight_color, label: view.values.label_color }))
      .catch(() => setColors(null));
  }, []);
  useEffect(loadColors, [loadColors]);

  const refresh = useCallback(async () => {
    if (!user) return;
    const [nextUploads, nextItems] = await Promise.all([api.uploads(user), api.items(user)]);
    setUploads(nextUploads);
    setItems(nextItems);
  }, [user]);

  useEffect(() => {
    api.columns().then(setColumns).catch(() => setNotice("Cannot reach the server."));
  }, []);

  const loadOutput = useCallback(() => {
    if (user) api.output(user).then(setOutput).catch(() => setOutput(null));
  }, [user]);
  useEffect(loadOutput, [loadOutput]);

  useEffect(() => {
    setSelected(null);
    void refresh();
  }, [refresh]);

  // Poll quickly while bills are being read, slowly otherwise so uploads
  // from another tab or by the API also show up.
  const busy = uploads.some((upload) => upload.status === "queued" || upload.status === "processing");
  useEffect(() => {
    const timer = window.setInterval(() => void refresh(), busy ? 2000 : 15000);
    return () => window.clearInterval(timer);
  }, [busy, refresh]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 6000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  // Open the first bill automatically once results arrive, and move on when
  // the open bill is deleted or cleared from the list.
  useEffect(() => {
    if (selected !== null && items.some((item) => item.id === selected)) return;
    const next = items.length ? items[0].id : null;
    if (next !== selected) setSelected(next);
  }, [items, selected]);

  const saveUser = (name: string) => {
    try {
      localStorage.setItem(USER_KEY, name);
    } catch {
      // private mode: keep it for this session only
    }
    setUser(name);
    setEditingUser(false);
  };

  const queueAction = async (action: () => Promise<unknown>) => {
    setNotice(null);
    try {
      await action();
    } catch (error) {
      setNotice((error as Error).message);
    }
    await refresh();
  };
  const removeAllUploads = () => {
    const waiting = uploads.filter((upload) => upload.status !== "done" && upload.status !== "processing").length;
    if (window.confirm(`Remove ${waiting} ${waiting === 1 ? "file" : "files"} that are waiting, paused or failed? Pages already read are discarded.`)) {
      void queueAction(() => api.removeAllUploads(user));
    }
  };
  const deleteItems = (ids: number[]) => {
    const chosen = items.filter((item) => ids.includes(item.id));
    const drafts = chosen.filter((item) => item.status === "draft").length;
    const submitted = chosen.length - drafts;
    const parts = [
      drafts ? `delete ${drafts} ${drafts === 1 ? "draft" : "drafts"} (OCR results and edits are discarded)` : "",
      submitted ? `remove ${submitted} submitted ${submitted === 1 ? "bill" : "bills"} from the list (they stay in the Excel files with their evidence PDFs)` : "",
    ].filter(Boolean);
    const question = parts.join(" and ");
    if (!question || !window.confirm(`${question.charAt(0).toUpperCase()}${question.slice(1)}?`)) return;
    void queueAction(() => api.deleteItems(user, ids));
  };
  const removeUpload = (id: number) => {
    const name = uploads.find((upload) => upload.id === id)?.filename ?? "this file";
    if (window.confirm(`Remove ${name} from the queue? Pages already read are discarded.`)) {
      void queueAction(() => api.removeUpload(id, user));
    }
  };

  const upload = async (files: File[], target: UploadTarget) => {
    setUploadFiles(null);
    setUploadTarget(target);
    setUploading(true);
    setNotice(null);
    try {
      const result = await api.upload(user, files, target);
      if (result.rejected.length) {
        setNotice(`Skipped: ${result.rejected.map((file) => `${file.filename} (${file.reason})`).join(", ")}`);
      }
      await refresh();
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const nextToReview = () => {
    const next = items.find((item) => item.id !== selected && item.status === "draft" && item.needs_review)
      ?? items.find((item) => item.id !== selected && item.status === "draft");
    if (next) setSelected(next.id);
  };

  if (!user || editingUser) return <UserGate initial={user} onDone={saveUser} />;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Taipower Bill OCR</h1>
          {output && (
            <span className="output" title={`Evidence PDFs: ${output.evidence_folder}`}>
              Submitted results go to {output.folder}
            </span>
          )}
        </div>
        <div className="topbar-actions">
          <a
            className="button"
            href={api.evidenceZipUrl(user)}
            title="Every evidence PDF in one ZIP: submitted/ holds what was sent, draft/ the current marks"
            onClick={(event) => {
              if (!items.length) {
                event.preventDefault();
                setNotice("No bills to download yet.");
              }
            }}
          >
            Download all PDFs
          </a>
          <button className={showDashboard ? "active" : ""} onClick={() => setShowDashboard(!showDashboard)}
            title="Grey and green power (solar, wind) per month and site">
            Dashboard
          </button>
          <a className="button" href={api.exportUrl(user)}>Export my Excel</a>
          <button onClick={() => setShowSettings(true)}>Settings</button>
          <button className="user" onClick={() => setEditingUser(true)} title="Change name">{user}</button>
        </div>
      </header>
      <div className="layout">
        <nav className="sidebar">
          <Dropzone onOpen={openUpload} busy={uploading} />
          {notice && <p className="notice">{notice}</p>}
          <UploadQueue
            uploads={uploads}
            onPause={() => void queueAction(() => api.pauseUploads(user))}
            onResume={() => void queueAction(() => api.resumeUploads(user))}
            onRetry={(id) => void queueAction(() => api.retryUpload(id, user))}
            onRemove={removeUpload}
            onRemoveAll={removeAllUploads}
          />
          <BillList items={items} selectedId={selected} onSelect={(id) => { setShowDashboard(false); setSelected(id); }} onDelete={deleteItems} />
        </nav>
        <main>
          {showDashboard ? (
            <Dashboard key={settingsVersion} onOpenSettings={() => setShowSettings(true)} />
          ) : selected !== null && columns.length ? (
            <Review
              key={selected}
              itemId={selected}
              user={user}
              columns={columns}
              onChanged={() => void refresh()}
              onNext={nextToReview}
              onNotify={setToast}
              colors={colors}
              settingsVersion={settingsVersion}
            />
          ) : (
            <div className="welcome">
              <h2>Drop Taipower bills on the left</h2>
              <p>Each bill is read automatically. Check the values, adjust the evidence marks, and submit — results go to the Excel files, with a marked-up PDF for each bill.</p>
            </div>
          )}
        </main>
      </div>
      {showSettings && (
        <SettingsDialog
          onClose={() => {
            setShowSettings(false);
            // Sites may have changed: bills show their site again.
            setSettingsVersion((version) => version + 1);
            void refresh();
          }}
          onSaved={() => { loadOutput(); loadColors(); }}
        />
      )}
      {uploadFiles !== null && (
        <UploadDialog
          sites={sites}
          initialFiles={uploadFiles}
          initialTarget={uploadTarget}
          onCancel={() => setUploadFiles(null)}
          onUpload={(files, target) => void upload(files, target)}
        />
      )}
      {toast && (
        <div className="toast" role="status" onClick={() => setToast(null)}>
          {toast}
        </div>
      )}
    </div>
  );
}
