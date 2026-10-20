import type { Column, Issue, Item, ItemSummary, Mark, OutputInfo, Row, SettingsSaved, SettingsValues, SettingsView, Site, Upload, ValueSource, BudgetRow, Contract, EnergyMix, Entity, Plan } from "./types";

type Sources = Record<string, ValueSource>;
/** Warnings the reviewer kept (the others were cleared). */
type Kept = {
  issues: Issue[];
  mark_notes: string[];
  site_id?: number | null;
  entity_id?: number | null;
  green?: {
    period: string;
    service?: string;
    lines?: Record<string, string | number | null | undefined>[];
    rows?: Record<string, string | number | null | undefined>[];
    values?: Record<string, string | number | null | undefined>;
    charges?: Record<string, string | number | null | undefined>;
  };
};
type Tags = { mark_tags: string[]; defaults: string[] };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const found = (await response.json()).detail;
      // A list of problems (e.g. a budget file's rows) is shown one per line.
      detail = typeof found === "string" ? found : found?.errors ? found.errors.join("\n") : found ? JSON.stringify(found) : detail;
    } catch {
      // not JSON; keep the status text
    }
    throw new Error(`${response.status} ${detail}`);
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  columns: () => request<Column[]>("/api/columns"),
  settings: () => request<SettingsView>("/api/settings"),
  dashboard: (submittedOnly: boolean) => request<EnergyMix>(`/api/dashboard?submitted_only=${submittedOnly}`),
  budget: () => request<{ rows: BudgetRow[]; can_edit: boolean }>("/api/budget"),
  budgetTemplateUrl: (year: number) => `/api/budget/template.xlsx?year=${year}`,
  importBudget: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ imported: number; months: string[] }>("/api/budget/import", { method: "POST", body: form });
  },
  plan: (year: number) => request<Plan>(`/api/plan?year=${year}`),
  savePlan: (update: { year: number; site: string; energy: string; annual_budget: number | null; adjustment_pct?: number;
    months?: Record<string, { kwh?: number | null; rate?: number | null }> }) =>
    request<Plan>("/api/plan", { method: "PUT", ...json(update) }),
  planTemplateUrl: (year: number) => `/api/plan/template.xlsx?year=${year}`,
  importPlan: (year: number, file: File) => {
    const form = new FormData();
    form.append("year", String(year));
    form.append("file", file);
    return request<{ imported: number }>("/api/plan/import", { method: "POST", body: form });
  },
  contracts: () => request<Contract[]>("/api/contracts"),
  saveContracts: (contracts: Contract[]) => request<Contract[]>("/api/contracts", { method: "PUT", ...json({ contracts }) }),
  saveSettings: (values: SettingsValues) => request<SettingsSaved>("/api/settings", { method: "PUT", ...json({ values }) }),
  browseFolder: () => request<{ folder: string | null }>("/api/settings/browse", { method: "POST" }),
  output: (user: string) => request<OutputInfo>(`/api/output?user=${encodeURIComponent(user)}`),

  /** ``target``: a site's id, "green" (green power documents), or null (each bill by its 電號). */
  upload: (user: string, files: File[], target: number | "green" | null = null) => {
    const form = new FormData();
    form.append("user", user);
    if (typeof target === "number") form.append("site_id", String(target));
    if (target === "green") form.append("kind", "green");
    files.forEach((file) => form.append("files", file, file.name));
    return request<{ accepted: { id: number; filename: string }[]; rejected: { filename: string; reason: string }[] }>(
      "/api/uploads",
      { method: "POST", body: form },
    );
  },

  uploads: (user: string) => request<Upload[]>(`/api/uploads?user=${encodeURIComponent(user)}`),
  pauseUploads: (user: string) => request<{ paused: number }>("/api/uploads/pause", { method: "POST", ...json({ user }) }),
  resumeUploads: (user: string) => request<{ resumed: number }>("/api/uploads/resume", { method: "POST", ...json({ user }) }),
  retryUpload: (id: number, user: string) => request<unknown>(`/api/uploads/${id}/retry`, { method: "POST", ...json({ user }) }),
  removeAllUploads: (user: string) => request<{ removed: number }>("/api/uploads/remove", { method: "POST", ...json({ user }) }),
  deleteItems: (user: string, ids: number[]) =>
    request<{ deleted: number[]; hidden: number[] }>("/api/items/delete", { method: "POST", ...json({ user, ids }) }),
  removeUpload: (id: number, user: string) =>
    request<unknown>(`/api/uploads/${id}?user=${encodeURIComponent(user)}`, { method: "DELETE" }),
  items: (user: string) => request<ItemSummary[]>(`/api/items?user=${encodeURIComponent(user)}`),
  item: (id: number) => request<Item>(`/api/items/${id}`),

  save: (id: number, user: string, row: Row, marks: Mark[], sources: Sources, kept: Kept) =>
    request<Item>(`/api/items/${id}`, { method: "PUT", ...json({ user, row, marks, sources, ...kept }) }),
  submit: (id: number, user: string, row: Row, marks: Mark[], sources: Sources, kept: Kept) =>
    request<Item>(`/api/items/${id}/submit`, { method: "POST", ...json({ user, row, marks, sources, ...kept }) }),
  sites: () => request<Site[]>("/api/sites"),
  entities: () => request<Entity[]>("/api/entities"),
  saveEntities: (entities: { id?: number; name: string; code: string; tax_id: string; note: string }[]) =>
    request<Entity[]>("/api/entities", { method: "PUT", ...json({ entities }) }),
  saveSites: (sites: { id?: number; name: string; code: string; note: string; entity_id: number | null; meters: string[] }[]) =>
    request<Site[]>("/api/sites", { method: "PUT", ...json({ sites }) }),
  tags: (user: string) => request<Tags>(`/api/preferences?user=${encodeURIComponent(user)}`),
  saveTags: (user: string, tags: string[] | null) =>
    request<Tags>("/api/preferences", { method: "PUT", ...json({ user, mark_tags: tags }) }),
  redetect: (id: number) => request<Item>(`/api/items/${id}/redetect`, { method: "POST" }),
  setKind: (id: number, kind: string) => request<Item>(`/api/items/${id}/kind`, { method: "POST", ...json({ kind }) }),
  reopen: (id: number, user: string) =>
    request<Item>(`/api/items/${id}/reopen`, { method: "POST", ...json({ user }) }),
  resetMarks: (id: number) => request<Item>(`/api/items/${id}/reset-marks`, { method: "POST" }),

  imageUrl: (id: number, version: string, page?: number | null) =>
    `/api/items/${id}/image?v=${encodeURIComponent(version)}${page ? `&page=${page}` : ""}`,
  evidencePngUrl: (id: number, version: number, page?: number | null) =>
    `/api/items/${id}/evidence.png?v=${version}${page ? `&page=${page}` : ""}`,
  evidencePdfUrl: (id: number, version: string) => `/api/items/${id}/evidence.pdf?v=${encodeURIComponent(version)}`,
  exportUrl: (user: string) => `/api/export.xlsx?user=${encodeURIComponent(user)}`,
  evidenceZipUrl: (user: string) => `/api/evidence.zip?user=${encodeURIComponent(user)}`,
};
