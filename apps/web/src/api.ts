import type { Column, Item, ItemSummary, Mark, OutputInfo, Row, Upload } from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
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
  output: (user: string) => request<OutputInfo>(`/api/output?user=${encodeURIComponent(user)}`),

  upload: (user: string, files: File[]) => {
    const form = new FormData();
    form.append("user", user);
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

  save: (id: number, user: string, row: Row, marks: Mark[]) =>
    request<Item>(`/api/items/${id}`, { method: "PUT", ...json({ user, row, marks }) }),
  submit: (id: number, user: string, row: Row, marks: Mark[]) =>
    request<Item>(`/api/items/${id}/submit`, { method: "POST", ...json({ user, row, marks }) }),
  reopen: (id: number, user: string) =>
    request<Item>(`/api/items/${id}/reopen`, { method: "POST", ...json({ user }) }),
  resetMarks: (id: number) => request<Item>(`/api/items/${id}/reset-marks`, { method: "POST" }),

  imageUrl: (id: number, page?: number | null) => `/api/items/${id}/image${page ? `?page=${page}` : ""}`,
  evidencePngUrl: (id: number, version: number, page?: number | null) =>
    `/api/items/${id}/evidence.png?v=${version}${page ? `&page=${page}` : ""}`,
  evidencePdfUrl: (id: number) => `/api/items/${id}/evidence.pdf`,
  exportUrl: (user: string) => `/api/export.xlsx?user=${encodeURIComponent(user)}`,
  evidenceZipUrl: (user: string) => `/api/evidence.zip?user=${encodeURIComponent(user)}`,
};
