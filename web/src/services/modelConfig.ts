/**
 * 模型参数能力表与配置序列化（F29）。
 *
 * 目的：让「只显示该方法实际使用的参数」成为一份可查的数据，而不是散落在界面里的 if。
 *
 * 依据（后端为唯一事实来源，前端只做镜像；改后端 DEFAULTS 时必须同步本文件）：
 *   - cellflow/runner.py:24–68   DEFAULTS / normalize_config（字段名、类型、取值范围）
 *   - cellflow/adapters.py       每个方法实际读取的 config 键（run_adapter 及各 _xxx 函数）
 *   - cellflow/registry.py       VELOCITY_MODELS（独立 scVelo 的 mode 由 method 决定）
 *
 * 三条规则：
 *   1. 只提交本方法会用到的键；不把「界面显示过」当成「提交过」。
 *   2. 被方法忽略的键不进 payload，让后端 DEFAULTS 生效，避免“看起来改了其实没改”。
 *   3. 导入的 JSON 必须逐项校验（类型 / 范围 / 未知键 / 方法归属），不合格整份拒绝，
 *      不做“能用多少用多少”的静默降级；数据声明（raw_x_confirmed 等）永远不从文件导入。
 */

import { readJson, writeJson } from './context';

// ------------------------------------------------------------------ 参数规格

export type ParamKind = 'int' | 'float' | 'bool' | 'text' | 'select';
export type ParamSection = 'science' | 'param';

export type ParamSpec = {
  key: string;
  /** 中文主标签 */
  label: string;
  /** 英文/术语（次级标签） */
  en?: string;
  hint: string;
  kind: ParamKind;
  section: ParamSection;
  min?: number;
  max?: number;
  step?: number;
  options?: { value: string; label: string }[];
  placeholder?: string;
  /** 默认折叠在「高级参数」里 */
  advanced?: boolean;
  /** 该键对应的后端前提 need（用于“本方法必需”标注） */
  need?: string;
};

/** 三个键与后端 runner.DEFAULTS 一一对应；取值范围照抄 normalize_config。 */
export const PARAMS: Record<string, ParamSpec> = {
  // ---- 科学输入（写进 config，影响预检或报告）
  root_cell: {
    key: 'root_cell', label: '起点细胞 root_cell', en: 'root cell', kind: 'text', section: 'science',
    need: 'root', placeholder: '例如 cell_001',
    hint: '数据中真实存在的细胞 ID；起点不同，拟时序顺序可能改变。',
  },
  root_reason: {
    key: 'root_reason', label: '起点依据 root_reason', en: 'root rationale', kind: 'text',
    section: 'science', need: 'root',
    hint: '为什么选它作为起点（实验或文献依据）；只填 ID 不会通过预检。',
  },
  group_key: {
    key: 'group_key', label: '分组列 group_key', en: 'group key', kind: 'text', section: 'science',
    need: 'groups',
    hint: 'obs 里的分组列；用于 PAGA / CellRank 的群标签。',
  },
  grn_path: {
    key: 'grn_path', label: '先验 GRN grn_path', en: 'prior GRN', kind: 'text', section: 'science',
    need: 'prior_GRN', placeholder: '例如 results/inputs/grn.csv',
    hint: 'CSV（TF 为行、target 为列，≤10 MiB）；不填则 RegVelo 不可运行。',
  },
  grn_source: {
    key: 'grn_source', label: 'GRN 来源 grn_source', en: 'GRN provenance', kind: 'text',
    section: 'science', need: 'prior_GRN',
    hint: '说明这张先验网络来自哪里（物种、方向、基因 ID 与版本）。',
  },
  grn_confirmed: {
    key: 'grn_confirmed', label: '先验 GRN 已核查 grn_confirmed', en: 'GRN checked',
    kind: 'bool', section: 'science', need: 'prior_GRN',
    hint: '勾选即声明已核对来源 / 物种 / 方向 / 基因 ID。',
  },
  mt_prefix: {
    key: 'mt_prefix', label: '线粒体前缀 mt_prefix', en: 'mitochondrial prefix', kind: 'text',
    section: 'science', placeholder: 'MT-',
    hint: '算线粒体占比用（大小写不敏感）。人和小鼠常用 MT-；Ensembl ID 需先映射，否则指标为 0。',
  },
  species: {
    key: 'species', label: '物种 species', en: 'species', kind: 'text', section: 'science',
    placeholder: 'human / mouse',
    hint: '只写进报告；程序不会自动推断。',
  },
  tissue: {
    key: 'tissue', label: '组织 tissue', en: 'tissue', kind: 'text', section: 'science',
    placeholder: '例如 骨髓 / PBMC',
    hint: '只写进报告，用于解释线粒体比例等指标；不参与计算。',
  },
  sample_key: {
    key: 'sample_key', label: '样本列 sample_key', en: 'sample key', kind: 'text',
    section: 'science',
    hint: '供体 / 样本列；未指定时细胞不能当作独立生物学重复。',
  },

  // ---- 计算参数
  seed: {
    key: 'seed', label: '随机种子 seed', en: 'random seed', kind: 'int', section: 'param',
    min: 0, max: 2147483647, step: 1, advanced: true,
    hint: '固定种子便于复现；不同种子会改变 UMAP/分群的细微结果。',
  },
  n_top_genes: {
    key: 'n_top_genes', label: '高变基因数 n_top_genes', en: 'HVG count', kind: 'int',
    section: 'param', min: 10, max: 10000, step: 100,
    hint: '参与降维与速度拟合的基因数；越大越慢。',
  },
  n_pcs: {
    key: 'n_pcs', label: '主成分数 n_pcs', en: 'principal components', kind: 'int',
    section: 'param', min: 2, max: 100, step: 1,
    hint: 'PCA 保留的主成分数，通常 20–50。',
  },
  n_neighbors: {
    key: 'n_neighbors', label: '邻居数 n_neighbors', en: 'neighbours', kind: 'int',
    section: 'param', min: 3, max: 200, step: 1,
    hint: '细胞邻接图邻居数；小数据 10–15 更敏感。',
  },
  min_shared_counts: {
    key: 'min_shared_counts', label: '最小共享计数 min_shared_counts',
    en: 'min shared counts', kind: 'int', section: 'param', min: 1, max: 1000, step: 1,
    hint: '剪接共同计数下限，过滤剪接信号过低的基因；过高会导致基因不足而报错。',
  },
  leiden_resolution: {
    key: 'leiden_resolution', label: '聚类分辨率 leiden_resolution', en: 'resolution',
    kind: 'float', section: 'param', min: 0.05, max: 5, step: 0.05,
    hint: '越大群越多；分辨率不同会得到不同的群。',
  },
  use_umap: {
    key: 'use_umap', label: '二维展示', en: 'UMAP', kind: 'bool', section: 'param',
    hint: '使用 UMAP（关闭时用 PCA）；只改变展示坐标，不改速度值。',
  },
  max_iter: {
    key: 'max_iter', label: 'dynamical 迭代上限 max_iter', en: 'EM iterations',
    kind: 'int', section: 'param', min: 1, max: 100, step: 1,
    hint: 'scVelo dynamical 的 EM 迭代上限；提高不保证收敛，只是给更多机会。',
  },
  max_epochs: {
    key: 'max_epochs', label: '训练轮数 max_epochs', en: 'epochs', kind: 'int',
    section: 'param', min: 1, max: 1000, step: 10,
    hint: '神经网络训练轮数（veloVI / RegVelo）；CPU 越慢越需要按收敛情况调整。',
  },
  cellrank_n_states: {
    key: 'cellrank_n_states', label: '宏观状态数 cellrank_n_states', en: 'macrostates',
    kind: 'int', section: 'param', min: 2, max: 10, step: 1,
    hint: 'CellRank GPCCA 的宏观状态数，通常 2–5；过多会把群切碎，终末状态需生物学确认。',
  },
  cellrank_terminal: {
    key: 'cellrank_terminal', label: '固定终末类别 cellrank_terminal', en: 'terminal categories',
    kind: 'text', section: 'param', placeholder: '例如 Beta,Alpha；留空自动选择',
    hint: '按分组类别名称填写，用英文逗号分隔。固定类别不等于已证实的生物学终末；重复、空项或未知类别会拒绝运行。',
  },
  device: {
    key: 'device', label: '计算设备 device', en: 'device', kind: 'select', section: 'param',
    options: [
      { value: 'auto', label: '自动（有 CUDA 用 GPU）' },
      { value: 'cpu', label: 'CPU' },
      { value: 'cuda', label: 'GPU（CUDA）' },
    ],
    hint: '纯 CPU 方法选 GPU 不会更快；veloVI / RegVelo 才有意义。',
  },
  velocity_model: {
    key: 'velocity_model', label: '速度模型 velocity_model', en: 'velocity mode',
    kind: 'select', section: 'param',
    options: [
      { value: 'stochastic', label: 'scVelo · stochastic（默认）' },
      { value: 'deterministic', label: 'scVelo · deterministic（稳态）' },
      { value: 'dynamical', label: 'scVelo · dynamical（完整动力学，最慢）' },
    ],
    hint: '仅一键分析使用；独立 scVelo 方法的 mode 由所选方法决定。veloVI 需单独运行。',
  },
  arrow_scale: {
    key: 'arrow_scale', label: '箭头长度缩放 arrow_scale', en: 'arrow scale', kind: 'float',
    section: 'param', min: 0.5, max: 3, step: 0.1, advanced: true,
    hint: '只影响报告图里箭头的显示长度，不改速度值或方向。',
  },
  arrow_density: {
    key: 'arrow_density', label: '箭头密度 arrow_density', en: 'arrow density', kind: 'int',
    section: 'param', min: 20, max: 500, step: 10, advanced: true,
    hint: '报告图每张最多画多少条箭头；越小越稀疏。',
  },
};

/** 所有运行都会读取的键（预检/报告用），任何方法都要提交。 */
export const ALWAYS_KEYS = ['mt_prefix', 'species', 'tissue', 'sample_key'];

/** 与方法无关、切换方法时保留用户已填写的值。 */
export const SHARED_KEYS = ['seed', 'mt_prefix', 'species', 'tissue', 'sample_key'];

const SCIENCE_KEYS = Object.values(PARAMS)
  .filter((spec) => spec.section === 'science')
  .map((spec) => spec.key);

/**
 * 方法 → 实际读取的 config 键。
 *
 * 逐条对照 adapters.run_adapter 与各分支函数；没有列出的键一律不显示也不提交。
 * 未登记的方法（计划方法、未来新增）只给数据级公共输入。
 */
export const METHOD_PARAMS: Record<string, string[]> = {
  // pca2：raw_x_confirmed / seed；mt_prefix 由 runner 传给 inspect；species/tissue/sample_key 进报告
  qc: ['seed', ...ALWAYS_KEYS],
  demo: ['seed', ...ALWAYS_KEYS],

  // _prep_expression/_scvelo_prepare + _leiden + _velocity_core + _fate_block + _diffmap_dpt
  auto: [
    'n_top_genes', 'n_pcs', 'n_neighbors', 'leiden_resolution', 'use_umap',
    'min_shared_counts', 'velocity_model', 'max_iter', 'cellrank_n_states', 'cellrank_terminal',
    'root_cell', 'root_reason', 'arrow_scale', 'arrow_density', 'seed', ...ALWAYS_KEYS,
  ],

  // _prep_expression + _leiden + _embedding
  leiden: [
    'n_top_genes', 'n_pcs', 'n_neighbors', 'leiden_resolution', 'use_umap',
    'seed', ...ALWAYS_KEYS,
  ],

  // _prep_expression + _diffmap_dpt + _embedding（group_key 仅用于给群排序）
  dpt: [
    'n_top_genes', 'n_pcs', 'n_neighbors', 'use_umap', 'seed',
    'root_cell', 'root_reason', 'group_key', ...ALWAYS_KEYS,
  ],

  // _prep_expression + _group_labels + PAGA（root_cell 仅用于方向线索）
  paga: [
    'n_top_genes', 'n_pcs', 'n_neighbors', 'use_umap', 'seed',
    'group_key', 'root_cell', ...ALWAYS_KEYS,
  ],

  // scVelo 独立分支：filter_genes / HVG / PCA / neighbours / moments / velocity(mode=method)
  scvelo_deterministic: [
    'min_shared_counts', 'n_top_genes', 'n_pcs', 'n_neighbors', 'use_umap',
    'arrow_scale', 'arrow_density', 'seed', ...ALWAYS_KEYS,
  ],
  scvelo_stochastic: [
    'min_shared_counts', 'n_top_genes', 'n_pcs', 'n_neighbors', 'use_umap',
    'arrow_scale', 'arrow_density', 'seed', ...ALWAYS_KEYS,
  ],
  // dynamical 额外调 recover_dynamics(max_iter=…)
  scvelo_dynamical: [
    'min_shared_counts', 'n_top_genes', 'n_pcs', 'n_neighbors', 'use_umap',
    'max_iter', 'arrow_scale', 'arrow_density', 'seed', ...ALWAYS_KEYS,
  ],

  // _prep_expression + _diffmap_dpt + _group_labels + GPCCA(cellrank_n_states)
  cellrank: [
    'n_top_genes', 'n_pcs', 'n_neighbors', 'use_umap', 'seed',
    'root_cell', 'root_reason', 'group_key', 'cellrank_n_states', 'cellrank_terminal', ...ALWAYS_KEYS,
  ],

  // _regvelo_prep + _regvelo(grn_path, grn_source, max_epochs, device)
  regvelo: [
    'min_shared_counts', 'n_pcs', 'n_neighbors', 'max_epochs', 'device', 'seed',
    'grn_path', 'grn_source', 'grn_confirmed', ...ALWAYS_KEYS,
  ],

  // _velovi(max_epochs, device) + HVG/PCA/neighbours
  velovi: [
    'min_shared_counts', 'n_top_genes', 'n_pcs', 'n_neighbors', 'use_umap',
    'max_epochs', 'device', 'seed', ...ALWAYS_KEYS,
  ],
};

/** 独立 scVelo 方法的 mode 由 method 决定（后端 adapters.py:703–704）。 */
export const MODE_BY_METHOD: Record<string, string> = {
  scvelo_deterministic: 'deterministic',
  scvelo_stochastic: 'stochastic',
  scvelo_dynamical: 'dynamical',
};

export function methodParamKeys(method: string): string[] {
  // 未登记的方法（计划方法 / 未来新增）只给随机种子与数据级公共输入；
  // 后端 DEFAULTS 会补齐其余键，不会因为缺少方法参数而报错。
  return METHOD_PARAMS[method] || ['seed', ...ALWAYS_KEYS];
}

/**
 * 界面上应当显示的键。
 * max_iter 只有 dynamical（含 auto 选 dynamical 时）才会被读到，其余情况隐藏。
 */
export function visibleParamKeys(method: string, config: Record<string, any>): string[] {
  const keys = methodParamKeys(method);
  return keys.filter((key) => {
    if (key === 'max_iter') {
      if (method === 'scvelo_dynamical') return true;
      if (method === 'auto') return String(config.velocity_model || '') === 'dynamical';
      return false;
    }
    return true;
  });
}

export function scienceKeysFor(method: string, config: Record<string, any>): string[] {
  const keys = visibleParamKeys(method, config);
  return SCIENCE_KEYS.filter((key) => keys.includes(key));
}

export function paramKeysFor(method: string, config: Record<string, any>): string[] {
  const keys = visibleParamKeys(method, config);
  return keys.filter((key) => !SCIENCE_KEYS.includes(key));
}

export function pick(source: Record<string, any>, keys: string[]): Record<string, any> {
  const out: Record<string, any> = {};
  for (const key of keys) {
    if (source && Object.prototype.hasOwnProperty.call(source, key)) out[key] = source[key];
  }
  return out;
}

// ------------------------------------------------------- 值校验与提交 payload

export type Checked = { ok: boolean; value?: any; error?: string };

export function checkValue(spec: ParamSpec, raw: unknown): Checked {
  switch (spec.kind) {
    case 'bool':
      return typeof raw === 'boolean'
        ? { ok: true, value: raw }
        : { ok: false, error: `${spec.label}（${spec.key}）必须是 true/false` };
    case 'int': {
      const value = typeof raw === 'number' ? raw : Number(raw);
      if (!Number.isFinite(value) || !Number.isInteger(value)) {
        return { ok: false, error: `${spec.label}（${spec.key}）必须是整数` };
      }
      if (value < (spec.min as number) || value > (spec.max as number)) {
        return { ok: false, error: `${spec.label}（${spec.key}）必须在 ${spec.min}–${spec.max} 之间` };
      }
      return { ok: true, value };
    }
    case 'float': {
      const value = typeof raw === 'number' ? raw : Number(raw);
      if (!Number.isFinite(value)) {
        return { ok: false, error: `${spec.label}（${spec.key}）必须是数字` };
      }
      if (value < (spec.min as number) || value > (spec.max as number)) {
        return { ok: false, error: `${spec.label}（${spec.key}）必须在 ${spec.min}–${spec.max} 之间` };
      }
      return { ok: true, value };
    }
    case 'select': {
      const value = String(raw ?? '');
      const allowed = (spec.options || []).map((item) => item.value);
      return allowed.includes(value)
        ? { ok: true, value }
        : { ok: false, error: `${spec.label}（${spec.key}）只能是 ${allowed.join(' / ')}` };
    }
    default: {
      if (typeof raw !== 'string') {
        return { ok: false, error: `${spec.label}（${spec.key}）必须是文本` };
      }
      if (spec.key === 'cellrank_terminal' && raw) {
        const labels = raw.split(',').map((s) => s.trim());
        if (labels.some((s) => !s) || new Set(labels).size !== labels.length) {
          return { ok: false, error: '固定终末类别不能重复或包含空类别' };
        }
      }
      return { ok: true, value: raw };
    }
  }
}

export type Declaration = { rawX: boolean; rawSU: boolean };

/**
 * 提交给 /api/runs 的 config：只含本方法实际读取的键 + 数据声明。
 * 空文本不提交（后端 DEFAULTS 就是空串，提交空串没有信息量）。
 */
export function buildSubmitPayload(
  method: string, config: Record<string, any>, declaration: Declaration,
): Record<string, any> {
  const payload: Record<string, any> = {};
  for (const key of visibleParamKeys(method, config)) {
    const spec = PARAMS[key];
    if (!spec) continue;
    const raw = config[key];
    if (raw === undefined || raw === null) continue;
    if (typeof raw === 'string' && !raw.trim() && spec.kind === 'text'
      && !(key === 'cellrank_terminal' && raw.length > 0)) continue;
    const checked = checkValue(spec, raw);
    if (!checked.ok) {
      if (key === 'cellrank_terminal') throw new Error(checked.error);
      continue;
    }
    payload[key] = checked.value;
  }
  // 数据声明来自「数据」页的人工核实，始终随任务提交（F07）。
  payload.raw_x_confirmed = Boolean(declaration.rawX);
  payload.raw_su_confirmed = Boolean(declaration.rawSU);
  return payload;
}

// --------------------------------------------------- 按方法隔离的本地草稿（F29）

/**
 * 存储结构：cf.draft.<数据版本键> = { schema: 2, byMethod: { <method>: config } }。
 * 与 F31 共用同一个键；旧版（schema 不存在、直接存 config）视为过期：切换方法时
 * 无法判断它属于哪个方法，宁可重新从服务端默认值开始，也不把 A 方法的参数带进 B 方法。
 */
const DRAFT_PREFIX = 'cf.draft.';
const DRAFT_SCHEMA = 2;

type DraftStore = {
  schema: number;
  byMethod: Record<string, Record<string, any>>;
  updatedAt: number;
};

function readDraftStore(versionKey: string): DraftStore | null {
  if (!versionKey) return null;
  const raw = readJson<DraftStore>(DRAFT_PREFIX + versionKey);
  if (!raw || typeof raw !== 'object') return null;
  if (raw.schema !== DRAFT_SCHEMA) return null;
  if (!raw.byMethod || typeof raw.byMethod !== 'object') return null;
  return raw;
}

export function readMethodDraft(versionKey: string, method: string): Record<string, any> | null {
  const store = readDraftStore(versionKey);
  const value = store?.byMethod?.[method];
  return value && typeof value === 'object' ? value : null;
}

export function saveMethodDraft(
  versionKey: string, method: string, config: Record<string, any>,
): void {
  if (!versionKey || !method) return;
  const store = readDraftStore(versionKey)
    || { schema: DRAFT_SCHEMA, byMethod: {}, updatedAt: 0 };
  store.byMethod[method] = config;
  store.updatedAt = Date.now();
  writeJson(DRAFT_PREFIX + versionKey, store);
}

export function draftMethodKeys(versionKey: string): string[] {
  const store = readDraftStore(versionKey);
  return store ? Object.keys(store.byMethod) : [];
}

// -------------------------------------------------- 版本化配置 JSON（导入/导出）

export const CONFIG_FILE_SCHEMA = 'cellflow.model-config';
export const CONFIG_FILE_VERSION = 1;

export type ModelConfigFile = {
  schema: string;
  version: number;
  exported_at: string;
  method: string;
  method_name: string;
  dataset: { id?: string; label?: string; version_key?: string };
  params: Record<string, unknown>;
  data_declaration: {
    raw_x_confirmed: boolean;
    raw_su_confirmed: boolean;
    note: string;
  };
  precheck: { ready: boolean; missing: string[]; blockers: string[] };
  note: string;
};

export function buildConfigFile(args: {
  method: string;
  methodName: string;
  dataset?: { id?: string; label?: string; versionKey?: string };
  config: Record<string, any>;
  declaration: Declaration;
  precheck: { ready: boolean; missing: string[]; blockers: string[] };
}): ModelConfigFile {
  const payload = buildSubmitPayload(args.method, args.config, args.declaration);
  const params: Record<string, unknown> = { ...payload };
  delete params.raw_x_confirmed;
  delete params.raw_su_confirmed;
  return {
    schema: CONFIG_FILE_SCHEMA,
    version: CONFIG_FILE_VERSION,
    exported_at: new Date().toISOString(),
    method: args.method,
    method_name: args.methodName,
    dataset: {
      id: args.dataset?.id,
      label: args.dataset?.label,
      version_key: args.dataset?.versionKey,
    },
    params,
    data_declaration: {
      raw_x_confirmed: Boolean(args.declaration.rawX),
      raw_su_confirmed: Boolean(args.declaration.rawSU),
      note: '原始计数声明不能通过配置文件导入：换机器/换数据后必须在「数据」页重新核实。',
    },
    precheck: {
      ready: Boolean(args.precheck.ready),
      missing: args.precheck.missing,
      blockers: args.precheck.blockers,
    },
    note: '只含方法参数；导入会先逐项校验，再按本机数据重新预检。',
  };
}

export type ParsedConfigFile = {
  ok: boolean;
  errors: string[];
  warnings: string[];
  method?: string;
  methodName?: string;
  params?: Record<string, any>;
};

/**
 * 解析并校验配置文件。任何一项不合格就整份拒绝（errors 非空），不做部分套用。
 */
export function parseConfigFile(
  text: string,
  options: { knownMethods: string[]; implemented?: string[]; currentDataset?: { id?: string; label?: string } },
): ParsedConfigFile {
  const errors: string[] = [];
  const warnings: string[] = [];
  const fail = (message: string): ParsedConfigFile => {
    errors.push(message);
    return { ok: false, errors, warnings };
  };

  const raw = (text || '').trim();
  if (!raw) return fail('文件是空的。');

  let data: any;
  try {
    data = JSON.parse(raw);
  } catch (exc) {
    return fail(`JSON 解析失败：${exc}`);
  }
  if (!data || typeof data !== 'object' || Array.isArray(data)) return fail('顶层必须是 JSON 对象。');

  if (data.schema !== CONFIG_FILE_SCHEMA) {
    return fail(`不是 CellFlow 模型配置（schema=${String(data.schema)}，期望 ${CONFIG_FILE_SCHEMA}）。`);
  }
  const version = Number(data.version);
  if (!Number.isInteger(version) || version < 1) return fail('version 必须是 ≥1 的整数。');
  if (version > CONFIG_FILE_VERSION) {
    return fail(`文件版本 v${version} 高于本程序支持的 v${CONFIG_FILE_VERSION}；请升级程序后再导入。`);
  }

  const method = typeof data.method === 'string' ? data.method.trim() : '';
  if (!method) return fail('缺少 method。');
  if (!options.knownMethods.includes(method)) {
    return fail(`文件中的方法「${method}」不在本机方法表里。`);
  }
  if (options.implemented && !options.implemented.includes(method)) {
    warnings.push(`「${method}」尚未接入：参数可以保存，但本机没有可调用的适配器，不能提交运行。`);
  }

  const params = data.params;
  if (!params || typeof params !== 'object' || Array.isArray(params)) {
    return fail('缺少 params（对象）。');
  }

  const allowed = new Set(methodParamKeys(method));
  const cleaned: Record<string, any> = {};
  for (const [key, value] of Object.entries(params)) {
    if (key === 'raw_x_confirmed' || key === 'raw_su_confirmed') {
      errors.push(`${key}：原始计数声明不能通过配置文件导入，请在「数据」页按当前数据确认。`);
      continue;
    }
    if (!allowed.has(key)) {
      errors.push(`${key}：不是「${method}」使用的参数（该方法的可用参数：${[...allowed].join('、')}）。`);
      continue;
    }
    const spec = PARAMS[key];
    if (!spec) {
      errors.push(`${key}：未知参数。`);
      continue;
    }
    const checked = checkValue(spec, value);
    if (!checked.ok) {
      errors.push(checked.error || `${key}：值不合法。`);
      continue;
    }
    cleaned[key] = checked.value;
  }

  if (data.data_declaration) {
    warnings.push('文件里的原始计数声明（raw_x_confirmed / raw_su_confirmed）已忽略：声明必须在「数据」页按当前数据重新确认。');
  }
  const fileDatasetId = data.dataset && typeof data.dataset === 'object' ? data.dataset.id : '';
  if (options.currentDataset?.id && fileDatasetId && fileDatasetId !== options.currentDataset.id) {
    warnings.push(`这份配置来自另一个数据集（${data.dataset.label || fileDatasetId}）；参数已套用，但本机数据的预检结果会重新计算。`);
  }
  if (errors.length) {
    return { ok: false, errors, warnings };
  }
  return {
    ok: true,
    errors,
    warnings,
    method,
    methodName: typeof data.method_name === 'string' ? data.method_name : method,
    params: cleaned,
  };
}
