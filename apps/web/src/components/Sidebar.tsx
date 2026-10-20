import { useEffect, useRef, useState } from "react";
import type { DragEvent } from "react";
import { api } from "../api";
import { periodMonth } from "../periods";
import type { ItemSummary, Site, Upload } from "../types";

const ACCEPT = ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp";

export function Dropzone({ onFiles, busy, sitesVersion }: { onFiles: (files: File[], target: number | "green" | null) => void; busy: boolean; sitesVersion: number }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [sites, setSites] = useState<Site[]>([]);
  const [target, setTarget] = useState<number | "green" | null>(null);
  useEffect(() => {
    api.sites().then(setSites).catch(() => undefined);
  }, [sitesVersion]);
  const send = (files: File[]) => onFiles(files, target);
  const drop = (event: DragEvent) => {
    event.preventDefault();
    setOver(false);
    const files = Array.from(event.dataTransfer.files);
    if (files.length) send(files);
  };
  return (
    <div className="upload-box">
      <label className="upload-site" title="Where the files uploaded next go: a site (every page in it), green power documents (Taipower's transfer lists, retailers' statements and notices: they cover several sites), or each bill by its 電號">
        For
        <select value={target ?? ""} onChange={(event) => {
          const value = event.target.value;
          setTarget(value === "green" ? "green" : value ? Number(value) : null);
        }}>
          <option value="">Bills — site by 電號</option>
          {sites.map((site) => <option key={site.id} value={site.id}>Site: {site.name}</option>)}
          <option value="green">綠電單據 (green power, all sites)</option>
        </select>
      </label>
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
            if (files.length) send(files);
          }}
        />
      </div>
    </div>
  );
}

type QueueActions = {
  onPause: () => void;
  onResume: () => void;
  onRetry: (id: number) => void;
  onRemove: (id: number) => void;
  onRemoveAll: () => void;
};

export function UploadQueue({ uploads, onPause, onResume, onRetry, onRemove, onRemoveAll }: { uploads: Upload[] } & QueueActions) {
  const active = uploads.filter((upload) => upload.status !== "done");
  if (!active.length) return null;
  const running = active.some((upload) => upload.status === "queued" || upload.status === "processing");
  const paused = active.some((upload) => upload.status === "paused");
  return (
    <section className="queue">
      <div className="queue-head">
        <h3>{running ? "Processing" : paused ? "Paused" : "Not processed"}</h3>
        <span className="queue-actions">
          {running && <button className="link" onClick={onPause} title="Stops after the page being read">Pause</button>}
          {!running && paused && <button className="link" onClick={onResume}>Resume</button>}
          {active.some((upload) => upload.status !== "processing") && (
            <button className="link danger" onClick={onRemoveAll} title="Remove every file that is waiting, paused or failed">
              Remove all
            </button>
          )}
        </span>
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

const GREEN_TYPES: Record<string, string> = {
  statement: "A retailer's green power statement",
  notice: "A retailer's purchase notice (企業購電電費通知單)",
  transfer_list: "Taipower's transfer fee list (轉供費用清單)",
};

interface BillListProps {
  items: ItemSummary[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  /** Delete drafts and clear submitted bills from the list. */
  onDelete: (ids: number[]) => void;
}

export function BillList({ items, selectedId, onSelect, onDelete }: BillListProps) {
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
  const row = (item: ItemSummary) => (
            <li key={item.id} className={item.id === selectedId ? "selected" : ""} onClick={() => onSelect(item.id)}>
              <span className={`dot ${item.status === "submitted" ? "done" : item.needs_review ? "warn" : "ok"}`} aria-hidden />
              <span className="bill-main">
                <span className="name">
                  {item.filename}{item.page ? ` · p${item.page}` : ""}
                  {item.kind === "other" && <span className="kind-badge" title="Not a Taipower bill: values found by keyword or filled in by hand">other</span>}
                  {item.kind === "green" && (
                    <span className="kind-badge green" title={GREEN_TYPES[item.green_type ?? "statement"]}>
                      {item.green_type === "transfer_list" ? "台電清單" : item.green_type === "notice" ? "購電通知" : "售電明細"}
                    </span>
                  )}
                </span>
                <span className="meta">
                  {item.kind === "green" && item.entity ? `${item.entity} · ` : ""}{item.billing_period ?? "period ?"} · {item.total !== null ? `NT$${item.total.toLocaleString()}` : "total ?"}
                </span>
              </span>
              <button
                className="icon remove"
                aria-label={`${item.status === "submitted" ? "Clear" : "Delete"} ${item.filename}`}
                title={item.status === "submitted" ? "Remove from the list (stays in the Excel files)" : "Delete this draft"}
                onClick={(event) => { event.stopPropagation(); onDelete([item.id]); }}
              >
                ✕
              </button>
            </li>
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
      {shown.length > 0 && (
        <div className="list-actions">
          <button
            className="link danger"
            onClick={() => onDelete(shown.map((item) => item.id))}
            title="Delete the drafts shown; submitted bills leave the list but stay in the Excel files"
          >
            Clear {filter === "all" ? "all" : "these"} ({shown.length})
          </button>
        </div>
      )}
      {shown.length === 0 ? (
        <p className="empty-list">{items.length ? "Nothing here." : "Upload bills to start."}</p>
      ) : (
        groupBySite(shown).map(([site, undated, months]) => (
          <details key={site} className="site-group" open>
            <summary>{site} <span>{undated.length + months.reduce((count, [, list]) => count + list.length, 0)}</span></summary>
            {undated.length > 0 && <ul>{undated.map(row)}</ul>}
            {months.map(([month, list]) => (
              <details key={month} className="month-group" open>
                <summary>{month} <span>{list.length}</span></summary>
                <ul>{list.map(row)}</ul>
              </details>
            ))}
          </details>
        ))
      )}
    </section>
  );
}

const NO_SITE = "No site";
const GREEN = "綠電 Green power";
const LAST = [GREEN, "Several sites", NO_SITE];

/** Items by site (sites by name, "No site" last), then by month (latest first); undated ones at the site's level. */
function groupBySite(items: ItemSummary[]): [string, ItemSummary[], [string, ItemSummary[]][]][] {
  const sites = new Map<string, ItemSummary[]>();
  // Green power documents cover several sites (a notice names only its entity): a group of their own.
  const groupOf = (item: ItemSummary) => (item.kind === "green" ? GREEN : item.site ?? NO_SITE);
  for (const item of items) sites.set(groupOf(item), [...(sites.get(groupOf(item)) ?? []), item]);
  return [...sites.entries()]
    // Sites by name, then documents over several sites, then those in none.
    .sort(([a], [b]) => (LAST.indexOf(a) - LAST.indexOf(b)) || a.localeCompare(b))
    .map(([site, list]) => {
      const months = new Map<string, ItemSummary[]>();
      const undated: ItemSummary[] = [];
      for (const item of list) {
        const month = periodMonth(item.billing_period);
        if (month) months.set(month, [...(months.get(month) ?? []), item]);
        else undated.push(item);
      }
      return [site, undated, [...months.entries()].sort(([a], [b]) => b.localeCompare(a))];
    });
}
