export type Row = Record<string, string | number | null>;

export interface Column {
  key: string;
  label: string;
  editable: boolean;
  numeric: boolean;
}

export interface Highlight {
  id: string;
  type: "highlight";
  x: number;
  y: number;
  w: number;
  h: number;
  group?: string;
  /** Another page of the PDF (the bill's back page); absent for the bill page. */
  page?: number;
  /** "#rrggbb"; absent follows the default colour in the settings. */
  color?: string;
  /** The value this mark is evidence for; picking a new value replaces it. */
  field?: string;
}

export interface Label {
  id: string;
  type: "label";
  x: number;
  y: number;
  text: string;
  fontSize: number;
  group?: string;
  page?: number;
  color?: string;
  field?: string;
}

export type Mark = Highlight | Label;

export interface Issue {
  zh: string;
  en: string;
  columns: string[];
}

export interface Upload {
  id: number;
  filename: string;
  status: "queued" | "processing" | "paused" | "done" | "failed";
  error: string | null;
  page_count: number | null;
  pages_done: number;
  created_at: string;
}

export interface ItemSummary {
  id: number;
  upload_id: number;
  filename: string;
  page: number | null;
  status: "draft" | "submitted";
  needs_review: boolean;
  issue_count: number;
  billing_period: string | null;
  customer_number: string | null;
  total: number | null;
  kind: DocumentKind;
  green_type?: GreenType | null;
  site: string | null;
  submitted_at: string | null;
}

export interface Site {
  id: number;
  name: string;
  code: string | null;
  note: string | null;
  /** 電號, digits only. */
  meters: string[];
}

/** A bill's site: chosen by hand, or the one its 電號 belongs to. */
export interface ItemSite extends Site {
  how: "manual" | "meter";
}

/** A bill's share of one calendar month (split by days). */
export interface MonthShare {
  月份: string;
  天數: number;
  用電度數: number | null;
  轉供度數: number | null;
  稅前應繳總金額: number | null;
  應繳總金額: number | null;
}

/** A Taipower bill, or another document (e.g. a landlord's allocation). */
export type DocumentKind = "taipower" | "other" | "green";

export interface GreenLine {
  meter: string | null;
  energy: string | null;
  base: number | null;
  transfer_rate: number | null;
  certificate_rate: number | null;
  unit_price: number | null;
  kwh: number | null;
  cost: number | null;
  check: { expected: number | null; difference: number | null; status: "ok" | "check" | "incomplete" };
  green: boolean;
}

export type GreenType = "statement" | "notice" | "transfer_list";

export interface TransferRowView {
  section: string | null;
  user_meter: string | null;
  user_device: string | null;
  generator: string | null;
  generator_device: string | null;
  kwh: number | null;
  rate: number | null;
  fee: number | null;
  note?: string | null;
  ours: boolean;
  check: { expected: number | null; difference: number | null; status: "ok" | "check" | "incomplete" };
}

/** How one meter's transferred kWh agrees across Taipower's list, the retailer and the bills. */
export interface MeterComparison {
  month: string;
  meter: string;
  site: string | null;
  services: string[];
  taipower_list_kwh: number | null;
  taipower_list_fees: number | null;
  retailer_kwh: number | null;
  retailer_difference: number | null;
  bill_kwh: number | null;
  bill_difference: number | null;
  bill_how: "same period" | "prorated" | "none" | "no period";
  retailer_bill_difference: number | null;
  bills: { id: number; filename: string; period: string | null; kwh: number }[];
}

/** A transfer service: a purchase notice's kWh (B) and fees (E) against Taipower's list. */
export interface ServiceComparison {
  month: string;
  service: string | null;
  has_list: boolean;
  /** The fee tables Taipower's list was read with (轉供輸電費清單, ...). */
  sections: string[];
  taipower_list_kwh: number;
  taipower_list_fees: number;
  notice: { kwh: number | null; supply_fees: number | null } | null;
  kwh_difference: number | null;
  fee_difference: number | null;
}

export interface GreenView {
  type: GreenType;
  type_name: string;
  period: string | null;
  service: string | null;
  tolerance: number;
  lines?: GreenLine[];
  total_kwh?: number;
  total_cost?: number | null;
  rows?: TransferRowView[];
  total_fees?: number;
  values?: Record<string, number | null>;
  comparisons: { meters: MeterComparison[]; services: ServiceComparison[]; sites_set: boolean };
}

/** Where a value came from. */
export type ValueSource = "taipower" | "keyword" | "pick" | "manual" | "alternative";

export interface OcrLine {
  text: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface Location {
  box: [number, number, number, number];
  exact: boolean;
  /** The page it is on, when not the item's own page. */
  page?: number | null;
}

export interface Item {
  id: number;
  user: string;
  filename: string;
  page: number | null;
  status: "draft" | "submitted";
  row: Row;
  ocr_row: Row;
  issues: Issue[];
  alternatives: Record<string, string | number>;
  locations: Record<string, Location>;
  marks: Mark[];
  mark_notes: string[];
  /** ``version`` tells uploads apart (ids are used again after a delete). */
  image: { width: number; height: number; dpi: number; version: string };
  /** The bill page first, then its back pages, which can hold marks too. */
  pages: ViewPage[];
  /** Default colours for marks without their own. */
  colors: MarkColors;
  kind: DocumentKind;
  sources: Record<string, ValueSource>;
  site: ItemSite | null;
  months: MonthShare[];
  /** A retailer's green power statement (kind "green"). */
  green: GreenView | null;
  submitted_at: string | null;
  submitted_by: string | null;
  locked_workbooks?: string[];
  archived_evidence?: string | null;
  evidence_file?: string;
}

export interface MarkColors {
  highlight: string;
  label: string;
}

export interface ViewPage {
  page: number | null;
  width: number;
  height: number;
  bill: boolean;
  /** OCR text on the page, for picking values on the image. */
  lines: OcrLine[];
}

export interface OutputInfo {
  workbooks: "shared" | "per_user";
  folder: string;
  evidence_folder: string;
}

export interface SettingsValues {
  output_dir: string;
  workbooks: "shared" | "per_user";
  ocr_backend: string;
  workbook_retry_seconds: number;
  host: string;
  port: number;
  highlight_color: string;
  label_color: string;
  green_tolerance: number;
  /** "1-11210-08-00210 太陽能": which energy each transfer service delivers. */
  green_services: string[];
  company_names: string[];
  self_terms: string[];
  period_keywords: string[];
  usage_keywords: string[];
  cost_keywords: string[];
  exclude_keywords: string[];
}

/** Grey and green power of a meter in a month (see the dashboard). */
export interface MixRow {
  month: string;
  /** null for a document without a 電號 (then ``document`` names it). */
  meter: string | null;
  document: string | null;
  site: string | null;
  site_code: string | null;
  grey_kwh: number | null;
  /** Green kWh by energy: 太陽能, 風力, 未分類 ... */
  green_kwh: Record<string, number>;
  green_total: number;
  total_kwh: number | null;
  green_share: number | null;
  grey_cost: number | null;
  green_cost: number | null;
  green_source: "transfer_list" | "statement" | "bill" | null;
}

export interface EnergyMix {
  rows: MixRow[];
  unclassified_services: string[];
  notes: string[];
  sites_set: boolean;
}

export type KeywordSetting = "company_names" | "self_terms" | "period_keywords" | "usage_keywords" | "cost_keywords" | "exclude_keywords";

export interface SettingsView {
  values: SettingsValues;
  /** Fixed by environment variables. */
  locked: (keyof SettingsValues)[];
  /** Only on the computer running the server. */
  can_edit: boolean;
  restart_required: string[];
  running: { host: string; port: number } | null;
  options: { workbooks: string[]; ocr_backend: string[] };
  dpi: number;
  data_dir: string;
  keyword_defaults: Record<KeywordSetting, string[]>;
}

export interface SettingsSaved extends SettingsView {
  changed: string[];
  restart: boolean;
  locked_workbooks: string[];
}
