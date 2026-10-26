import { requestJson } from './net';

export type Dataset = {
  id: string; path: string; label: string; source: string; kind: string; size: string; mtime: number;
};
export type Run = {
  id: string; name?: string; method: string; method_label: string; state: string;
  submitted: number; updated: number; output: string; input: string;
  dataset?: string; error: string;
};
export type MethodRow = {
  key: string; name: string; status: string; can_run: boolean; needs: string[];
  packages: string[]; missing: string[]; blockers: string[]; family: string;
};
export type Project = { id: string; name: string; note?: string; created?: string; archived?: boolean };
export type Meta = {
  project: { name: string; created?: string };
  projects?: Project[];
  active_project?: Project;
  datasets: Dataset[];
  counts: Record<string, number>;
  methods: { key: string; name: string; family: string; status: string; needs: string[]; packages: string[] }[];
  velocity_models: { key: string; label: string; speed: string; gpu: string }[];
  default_velocity_model: string;
};
export type Inspect = {
  path: string; id: string;
  format: {
    shape?: { n_cells: number; n_genes: number };
    layers_present?: string[];
    x_kind_text?: string;
    splice?: { both_positive_pct: number } | null;
    var?: { mt_matched?: number };
    obs?: string[];
    warnings?: string[];
  };
  facts: {
    n_cells: number; n_genes: number; x_kind: string; x_kind_text: string;
    warnings: string[]; errors: string[]; velocity_counts?: unknown;
    group_present?: string | null; obs_keys?: string[]; synthetic?: boolean;
  };
  methods: MethodRow[];
  config: Record<string, unknown>;
};
export type EnvInfo = {
  python: string;
  executable: string;
  platform: string;
  versions: Record<string, string | null>;
  device: { device: string; available: boolean; reason: string; gpu_names?: string[] };
  disk: { total_gb: number; free_gb: number; results_gb: number };
  paths: Record<string, string>;
  web_built: boolean;
  queue: Record<string, number>;
};

/** P3-01：GEO 补充文件清单里的一条（不含任何下载行为）。 */
export type GeoFile = {
  name: string; url: string; level: 'series' | 'sample' | string; accession: string;
  size_bytes: number | null; size_text: string; size_source: string;
  kind: string; compression: string; importable: boolean;
  /** verified_by_content / metadata_supported / filename_only / absent —— 只有内容验证过才算存在。 */
  splice_evidence: string; splice_filename_hint: boolean;
  recommended_for: string[]; download_state: string; modified: string;
};

export type GeoManifest = {
  ok: boolean; accession: string; accession_kind?: string;
  info: {
    ok?: boolean; uid?: string; accession?: string; title?: string; summary?: string;
    taxon?: string; n_samples?: number | string; pdat?: string; gdstype?: string;
    entry_type?: string; source?: string; reason?: string;
  };
  files: GeoFile[]; file_count: number; total_bytes: number; total_bytes_known: boolean;
  dir_url: string; official_page: string; note: string;
};

/** P3-02：数据与存储状态。 */
export type StorageInfo = {
  ok: boolean; data_root: string; is_default: boolean; configured: string;
  env_override: boolean; exists: boolean; writable: boolean;
  free_bytes: number | null; total_bytes: number | null;
  paths: Record<string, string>;
  staging: { total_bytes: number; count: number; items: unknown[]; error?: string };
  settings_file: string;
};

/** 后端 downloads.list_tasks() 的返回项（含创建时间，用于失败后再核对）。 */
export type DownloadTask = {
  id: string; source: string; identifier: string; project?: string;
  state: string; done: number; total: number; message: string;
  path: string; error: string; created: number; updated: number;
};

export type RunDetail = {
  id: string; method: string; method_label: string; state: string; error: string;
  submitted: number; updated: number; output: string; input: string; dataset?: string;
  config: any; result: any; qc: any; provenance: any;
  figures: string[]; reports: Record<string, boolean>;
  artifacts?: { a_report: boolean; b_report: boolean; result: boolean };
  name?: string;
  stage?: string;
  elapsed_seconds?: number;
  quality?: {
    checks: { check: string; ok: boolean; detail?: string }[];
    warnings: string[];
    ok: boolean;
  };
};

export type RunCompare = {
  a: string; b: string;
  changes: { key: string; a: unknown; b: unknown }[];
  same: boolean;
  metric_changes?: { key: string; a: unknown; b: unknown }[];
  figures?: { a: string[]; b: string[] };
  /** 两张嵌入图能否按同一坐标直接比较（要求输入/预处理/参数/实际坐标都一致）。 */
  can_compare_figures?: boolean;
  /** 不能同坐标比较时的逐条原因（界面逐条展示，不合并成一句话）。 */
  reasons?: string[];
  compare_reason?: string;
  side_by_side?: boolean;
  same_coordinates?: boolean;
  same_input_identity?: boolean;
  metrics_comparable?: boolean;
  metrics_note?: string;
  input_identity?: {
    cells_hash?: string | null; genes_hash?: string | null;
    preprocessing_hash?: string | null; embedding_hash?: string | null;
  };
};

// 统一走 services/net.ts：
//   - fetch 抛出的 TypeError('Failed to fetch') 被转成可判断的 ApiRequestError(kind='transport')，
//     界面据此显示「本机服务不可达」，而不是把浏览器原始英文回显给用户；
//   - HTTP 非 2xx 保留后端 detail 与状态码（kind='http'）；
//   - AbortError 原样抛出，取消逻辑不变。
const request = requestJson;

// 可选 AbortSignal：调用方切换数据/任务时取消旧请求，避免旧响应覆盖新上下文。
function signalOptions(signal?: AbortSignal): RequestInit | undefined {
  return signal ? { signal } : undefined;
}

export const api = {
  meta: (signal?: AbortSignal) => request<Meta>('/api/meta', signalOptions(signal)),
  inspect: (path: string, rawX = false, rawSU = false, signal?: AbortSignal) =>
    request<Inspect>(`/api/datasets/inspect?path=${encodeURIComponent(path)}`
      + `&raw_x=${rawX ? 1 : 0}&raw_su=${rawSU ? 1 : 0}`, signalOptions(signal)),
  upload: async (file: File) => {
    const form = new FormData();
    form.append('file', file);
    return request<{ ok: boolean; path: string; id: string }>('/api/datasets/upload', {
      method: 'POST', body: form,
    });
  },
  demo: () => request<{ ok: boolean; path: string; id: string }>('/api/datasets/demo', { method: 'POST' }),
  publicFetch: (source: string, identifier: string) =>
    request<{ ok: boolean; files: string[] }>('/api/datasets/public', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, identifier }),
    }),
  runs: (params?: {
    state?: string; method?: string; dataset?: string; q?: string; limit?: number; offset?: number;
  }, signal?: AbortSignal) => {
    const search = new URLSearchParams();
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
    });
    const suffix = search.toString();
    // F33：服务端分页。total 是本项目内符合条件的总数（不是本页条数），
    // limit 默认 100 / 上限 200，offset 生效。
    return request<{
      runs: Run[]; total: number; limit: number; offset: number; has_more: boolean;
    }>('/api/runs' + (suffix ? '?' + suffix : ''), signalOptions(signal));
  },
  cancel: (id: string) => request<{ ok: boolean; message: string }>(`/api/runs/${id}/cancel`, { method: 'POST' }),
  run: (id: string, signal?: AbortSignal) =>
    request<RunDetail>(`/api/runs/${id}`, signalOptions(signal)),
  createRun: (path: string, method: string, config: Record<string, unknown>) =>
    request<{ ok: boolean; id: string }>('/api/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, method, config }),
    }),
  log: (id: string, signal?: AbortSignal) =>
    request<{ log: string }>(`/api/logs/${id}`, signalOptions(signal)),
  report: (id: string, kind: string, signal?: AbortSignal) =>
    fetch(`/api/runs/${id}/report/${kind}`, signalOptions(signal)).then((r) => {
      if (!r.ok) throw new Error('报告不存在');
      return r.text();
    }),
  startDownload: (source: string, identifier: string) =>
    request<{ ok: boolean; id: string }>('/api/downloads', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, identifier }),
    }),
  // GET 幂等：POST 在传输层失败后用它核对任务是否其实已经建好，
  // 避免盲目重发一次会重复建任务的 POST。
  downloads: () => request<{ downloads: DownloadTask[] }>('/api/downloads'),
  downloadStatus: (id: string) => request<{
    id: string; state: string; done: number; total: number;
    message: string; path: string; error: string; created?: number; updated?: number;
  }>(`/api/downloads/${id}`),
  cancelDownload: (id: string) =>
    request<{ ok: boolean; message: string }>(`/api/downloads/${id}/cancel`, { method: 'POST' }),
  // ---- P3-01：先预览、再下载用户勾选的文件（不会自动下载整个 Series）
  geoManifest: (gse: string, signal?: AbortSignal) =>
    request<GeoManifest>(`/api/public/geo/${encodeURIComponent(gse)}/manifest`, signalOptions(signal)),
  geoDownload: (gse: string, files: string[]) =>
    request<{ ok: boolean; ids: string[]; count: number; files: string[]; note: string }>(
      '/api/public/geo/download', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gse, files }),
      }),
  // ---- P3-02：数据根目录与 staging 占用
  storage: () => request<StorageInfo>('/api/storage'),
  setDataRoot: (path: string, confirm: boolean, create = true) =>
    request<{ ok: boolean; data_root: string; note: string }>('/api/storage/data-root', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, confirm, create }),
    }),
  cleanupStorage: (maxAgeHours = 24, dryRun = true) =>
    request<{ ok: boolean; dry_run: boolean; removed: unknown[]; removed_bytes: number }>(
      '/api/storage/cleanup', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ max_age_hours: maxAgeHours, dry_run: dryRun }),
      }),
  health: () => request<{
    ok: boolean; root: string; queue: Record<string, number>;
    device: string; projects: string;
  }>('/api/health'),
  env: () => request<EnvInfo>('/api/env'),
  createProject: (name: string) =>
    request<{ ok: boolean; project: Project }>('/api/projects', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),
  activateProject: (id: string) =>
    request<{ ok: boolean; project: Project }>(`/api/projects/${id}/activate`, { method: 'POST' }),
  archiveProject: (id: string, archived: boolean) =>
    request<{ ok: boolean; project: Project }>(
      `/api/projects/${id}/archive?archived=${archived ? 1 : 0}`, { method: 'POST' }),
  deleteProject: (id: string) =>
    request<{ ok: boolean; message: string }>(`/api/projects/${id}`, { method: 'DELETE' }),
  renameRun: (id: string, name: string) =>
    request<{ ok: boolean; name: string }>(`/api/runs/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),
  deleteRun: (id: string) => request<{ ok: boolean; message: string }>(`/api/runs/${id}`, { method: 'DELETE' }),
  removeDataset: (id: string) =>
    request<{ ok: boolean; message: string }>(`/api/datasets/${id}`, { method: 'DELETE' }),
  datasetDetail: (id: string, signal?: AbortSignal) => request<{
    id: string; label: string; source: string; kind: string; size: string;
    mtime: number; path: string; content_hash: string; hash_kind: string;
    n_cells: number; n_genes: number; layers: string[]; obs: string[];
    x_kind: string; warnings: string[];
    tasks: { id: string; name: string; state: string }[]; task_count: number;
    memory?: { gb: number; available_gb: number; method: string; ratio: number };
    preview?: {
      stats: { min: number; max: number; mean: number; nonzero_pct: number; dtype: string; sparse: boolean };
      cells: string[]; genes: string[]; layers: string[]; obs_preview: Record<string, string[]>;
      obs_summary: Record<string, { unique: number; top: { value: string; count: number; zh?: string | null }[] }>;
    };
  }>(`/api/datasets/${id}/detail`, signalOptions(signal)),
  pdfUrl: (id: string, kind: 'a' | 'b') => `/api/runs/${id}/report/${kind}/pdf`,
  compareRuns: (id: string, other: string, signal?: AbortSignal) =>
    request<RunCompare>(`/api/runs/${id}/compare/${other}`, signalOptions(signal)),
  renameProject: (name: string) =>
    request<{ ok: boolean }>('/api/project', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),
  figureUrl: (id: string, name: string) => `/api/runs/${id}/figure/${name}`,
  zipUrl: (id: string) => `/api/runs/${id}/zip`,
};
