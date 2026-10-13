import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { Review } from "./components/Review";
import { BillList, Dropzone, UploadQueue } from "./components/Sidebar";
import type { Column, ItemSummary, Upload } from "./types";

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

  const refresh = useCallback(async () => {
    if (!user) return;
    const [nextUploads, nextItems] = await Promise.all([api.uploads(user), api.items(user)]);
    setUploads(nextUploads);
    setItems(nextItems);
  }, [user]);

  useEffect(() => {
    api.columns().then(setColumns).catch(() => setNotice("Cannot reach the server."));
  }, []);

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

  // Open the first bill automatically once results arrive.
  useEffect(() => {
    if (selected === null && items.length) setSelected(items[0].id);
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

  const upload = async (files: File[]) => {
    setUploading(true);
    setNotice(null);
    try {
      const result = await api.upload(user, files);
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
        <h1>Taipower Bill OCR</h1>
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
          <a className="button" href={api.exportUrl(user)}>Export my Excel</a>
          <button className="user" onClick={() => setEditingUser(true)} title="Change name">{user}</button>
        </div>
      </header>
      <div className="layout">
        <nav className="sidebar">
          <Dropzone onFiles={upload} busy={uploading} />
          {notice && <p className="notice">{notice}</p>}
          <UploadQueue uploads={uploads} />
          <BillList items={items} selectedId={selected} onSelect={setSelected} />
        </nav>
        <main>
          {selected !== null && columns.length ? (
            <Review
              key={selected}
              itemId={selected}
              user={user}
              columns={columns}
              onChanged={() => void refresh()}
              onNext={nextToReview}
              onNotify={setToast}
            />
          ) : (
            <div className="welcome">
              <h2>Drop Taipower bills on the left</h2>
              <p>Each bill is read automatically. Check the values, adjust the evidence marks, and submit — results go to the shared Excel files, with a marked-up PDF for each bill.</p>
            </div>
          )}
        </main>
      </div>
      {toast && (
        <div className="toast" role="status" onClick={() => setToast(null)}>
          {toast}
        </div>
      )}
    </div>
  );
}
