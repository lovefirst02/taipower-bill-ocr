import { useRef, useState } from "react";
import type { DragEvent } from "react";
import type { ItemSummary, Upload } from "../types";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp";

export function Dropzone({ onFiles, busy }: { onFiles: (files: File[]) => void; busy: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const drop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length) onFiles(files);
  };
  return (
    <div
      className={`dropzone${over ? " over" : ""}`}
      role="button"
      tabIndex={0}
      onClick={() => input.current?.click()}
      onKeyDown={(event) => (event.key === "Enter" || event.key === " ") && input.current?.click()}
      onDragOver={(event) => { event.preventDefault(); setOver(true); }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <strong>{busy ? "Uploading…" : "Drop bills here"}</strong>
      <span>or click to choose · PDF, PNG, JPG · many at once</span>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        hidden
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = "";
          if (files.length) onFiles(files);
        }}
      />
    </div>
  );
}

type QueueActions = {
  onPause: () => void;
  onResume: () => void;
  onRetry: (id: number) => void;
  onRemove: (id: number) => void;
};

export function UploadQueue({ uploads, onPause, onResume, onRetry, onRemove }: { uploads: Upload[] } & QueueActions) {
  const active = uploads.filter((upload) => upload.status !== "done");
  if (!active.length) return null;
  const running = active.some((upload) => upload.status === "queued" || upload.status === "processing");
  const paused = active.some((upload) => upload.status === "paused");
  return (
    <section className="queue">
      <div className="queue-head">
        <h3>{running ? "Processing" : paused ? "Paused" : "Not processed"}</h3>
        {running && <button className="link" onClick={onPause} title="Stops after the page being read">Pause</button>}
        {!running && paused && <button className="link" onClick={onResume}>Resume</button>}
      </div>
      <ul>
        {active.map((upload) => {
          const total = upload.page_count ?? 0;
          const percent = total ? Math.round((upload.pages_done / total) * 100) : 0;
          const removable = upload.status !== "processing";
          return (
            <li key={upload.id} className={upload.status}>
              <span className="name" title={upload.filename}>{upload.filename}</span>
              {upload.status === "failed" ? (
                <span className="error" title={upload.error ?? ""}>
                  Failed <button className="link" onClick={() => onRetry(upload.id)}>Retry</button>
                </span>
              ) : (
                <>
                  <span className="progress"><span style={{ width: `${upload.status === "queued" ? 0 : percent}%` }} /></span>
                  <span className="state">
                    {upload.status === "queued"
                      ? "Queued"
                      : upload.status === "paused"
                        ? total ? `Paused ${upload.pages_done}/${total}` : "Paused"
                        : total ? `${upload.pages_done}/${total}` : "Reading…"}
                  </span>
                </>
              )}
              {removable ? (
                <button className="icon" onClick={() => onRemove(upload.id)} title="Remove from the queue" aria-label={`Remove ${upload.filename}`}>✕</button>
              ) : <span />}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

type Filter = "all" | "review" | "draft" | "submitted";

export function BillList({ items, selectedId, onSelect }: { items: ItemSummary[]; selectedId: number | null; onSelect: (id: number) => void }) {
  const [filter, setFilter] = useState<Filter>("all");
  const counts = {
    all: items.length,
    review: items.filter((item) => item.status === "draft" && item.needs_review).length,
    draft: items.filter((item) => item.status === "draft").length,
    submitted: items.filter((item) => item.status === "submitted").length,
  };
  const shown = items.filter((item) =>
    filter === "all" ? true : filter === "review" ? item.status === "draft" && item.needs_review : item.status === filter,
  );
  return (
    <section className="bills">
      <div className="filters" role="tablist">
        {(["all", "review", "draft", "submitted"] as Filter[]).map((name) => (
          <button key={name} role="tab" aria-selected={filter === name} className={filter === name ? "active" : ""} onClick={() => setFilter(name)}>
            {name === "review" ? "Needs review" : name[0].toUpperCase() + name.slice(1)} <span>{counts[name]}</span>
          </button>
        ))}
      </div>
      {shown.length === 0 ? (
        <p className="empty-list">{items.length ? "Nothing here." : "Upload bills to start."}</p>
      ) : (
        <ul>
          {shown.map((item) => (
            <li key={item.id} className={item.id === selectedId ? "selected" : ""} onClick={() => onSelect(item.id)}>
              <span className={`dot ${item.status === "submitted" ? "done" : item.needs_review ? "warn" : "ok"}`} aria-hidden />
              <span className="bill-main">
                <span className="name">{item.filename}{item.page ? ` · p${item.page}` : ""}</span>
                <span className="meta">{item.billing_period ?? "period ?"} · {item.total !== null ? `NT$${item.total.toLocaleString()}` : "total ?"}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
