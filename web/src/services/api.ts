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

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  meta: () => request<Meta>('/api/meta'),
  inspect: (path: string, rawX = false, rawSU = false) =>
    request<Inspect>(`/api/datasets/inspect?path=${encodeURIComponent(path)}`
      + `&raw_x=${rawX ? 1 : 0}&raw_su=${rawSU ? 1 : 0}`),
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
  runs: (params?: { state?: string; method?: string; dataset?: string; q?: string; limit?: number }) => {
    const search = new URLSearchParams();
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
    });
    const suffix = search.toString();
    return request<{ runs: Run[] }>('/api/runs' + (suffix ? '?' + suffix : ''));
  },
  cancel: (id: string) => request<{ ok: boolean; message: string }>(`/api/runs/${id}/cancel`, { method: 'POST' }),
  run: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  createRun: (path: string, method: string, config: Record<string, unknown>) =>
    request<{ ok: boolean; id: string }>('/api/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, method, config }),
    }),
  log: (id: string) => request<{ log: string }>(`/api/logs/${id}`),
  report: (id: string, kind: string) => fetch(`/api/runs/${id}/report/${kind}`).then((r) => {
    if (!r.ok) throw new Error('报告不存在');
    return r.text();
  }),
  startDownload: (source: string, identifier: string) =>
    request<{ ok: boolean; id: string }>('/api/downloads', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, identifier }),
    }),
  downloadStatus: (id: string) => request<{
    id: string; state: string; done: number; total: number;
    message: string; path: string; error: string;
  }>(`/api/downloads/${id}`),
  cancelDownload: (id: string) =>
    request<{ ok: boolean; message: string }>(`/api/downloads/${id}/cancel`, { method: 'POST' }),
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
  datasetDetail: (id: string) => request<{
    id: string; label: string; source: string; kind: string; size: string;
    mtime: number; path: string; sha256: string;
    n_cells: number; n_genes: number; layers: string[]; obs: string[];
    x_kind: string; warnings: string[];
    tasks: { id: string; name: string; state: string }[]; task_count: number;
    memory?: { gb: number; available_gb: number; method: string; ratio: number };
    preview?: {
      stats: { min: number; max: number; mean: number; nonzero_pct: number; dtype: string; sparse: boolean };
      cells: string[]; genes: string[]; layers: string[]; obs_preview: Record<string, string[]>;
      obs_summary: Record<string, { unique: number; top: { value: string; count: number; zh?: string | null }[] }>;
    };
  }>(`/api/datasets/${id}/detail`),
  pdfUrl: (id: string, kind: 'a' | 'b') => `/api/runs/${id}/report/${kind}/pdf`,
  compareRuns: (id: string, other: string) =>
    request<{
      a: string; b: string;
      changes: { key: string; a: unknown; b: unknown }[];
      same: boolean;
      metric_changes?: { key: string; a: unknown; b: unknown }[];
      figures?: { a: string[]; b: string[] };
      can_compare_figures?: boolean;
      compare_reason?: string;
    }>(`/api/runs/${id}/compare/${other}`),
  renameProject: (name: string) =>
    request<{ ok: boolean }>('/api/project', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    }),
  figureUrl: (id: string, name: string) => `/api/runs/${id}/figure/${name}`,
  zipUrl: (id: string) => `/api/runs/${id}/zip`,
};
