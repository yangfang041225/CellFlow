import type { Dataset } from './api';

/**
 * 数据上下文：数据集版本键、科研声明、参数草稿与下载记录的本地持久化。
 *
 * F07：科研声明必须按“数据内容版本”隔离。
 *   - 只用 dataset.id（路径哈希）不够：同一路径的数据文件被覆盖后内容会变；
 *   - 只用 mtime 也不够：不同目录的文件可能 mtime 相同；
 *   - 因此用 dataset.id + mtime 组成版本键，切换数据集不会继承另一份数据的声明。
 *   旧版本把 raw_x/raw_su 放在整个 origin 共享的固定键上，这里一次性丢弃，
 *   确保升级后必须按新数据重新确认。
 *
 * F31：参数草稿也按版本键保存；切换数据集时先用旧键写回，再加载新键，
 *   避免“草稿还没加载完就把旧 config 写到新数据集名下”。
 */

/** 与后端 cellflow/data.py 的 AUTO_GROUP 保持一致：用自动分群产生群标签。 */
export const AUTO_GROUP = '__auto_leiden__';

const DECLARATIONS_KEY = 'cf.declarations';
const DECLARATION_LEGACY_KEYS = ['cf.raw_x', 'cf.raw_su'];
const DRAFT_PREFIX = 'cf.draft.';
const DOWNLOAD_KEY = 'cf.download.last';

export type Declaration = {
  /** X 是否为未归一化原始计数（人工核实后勾选）。 */
  rawX: boolean;
  /** spliced/unspliced 是否为原始剪接计数（人工核实后勾选）。 */
  rawSU: boolean;
  /** 最近一次确认时间（毫秒时间戳）；0 表示尚未确认。 */
  confirmedAt: number;
  /** 确认时对应的数据路径（便于追查）。 */
  path: string;
  /** 确认时对应的数据 mtime（版本核对）。 */
  mtime: number;
};

export const EMPTY_DECLARATION: Declaration = {
  rawX: false,
  rawSU: false,
  confirmedAt: 0,
  path: '',
  mtime: 0,
};

export type DownloadRecord = {
  id: string;
  startedAt: number;
  source?: string;
  identifier?: string;
};

export function readJson<T>(key: string): T | null {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function writeJson(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* 隐私模式或配额不足时忽略：界面仍可工作，只是不持久化 */
  }
}

export function removeKey(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* 忽略 */
  }
}

/**
 * 数据内容版本键：id（路径哈希）+ mtime（文件修改时间）。
 * 数据文件被替换后 mtime 变化 => 新版本 => 需要重新确认声明。
 */
export function datasetVersionKey(dataset?: Dataset): string {
  if (!dataset) return '';
  const id = dataset.id || dataset.path || '';
  if (!id) return '';
  return `${id}@${Number(dataset.mtime || 0)}`;
}

/** 读取全部声明；顺带清掉旧版全局键，避免跨数据集继承。 */
export function loadDeclarations(): Record<string, Declaration> {
  const raw = readJson<Record<string, unknown>>(DECLARATIONS_KEY);
  const out: Record<string, Declaration> = {};
  if (raw && typeof raw === 'object') {
    for (const [key, value] of Object.entries(raw)) {
      if (!value || typeof value !== 'object') continue;
      const item = value as Record<string, unknown>;
      out[key] = {
        rawX: item.rawX === true,
        rawSU: item.rawSU === true,
        confirmedAt: Number(item.confirmedAt) || 0,
        path: String(item.path || ''),
        mtime: Number(item.mtime) || 0,
      };
    }
  }
  DECLARATION_LEGACY_KEYS.forEach(removeKey);
  return out;
}

export function saveDeclarations(map: Record<string, Declaration>): void {
  writeJson(DECLARATIONS_KEY, map);
}

/** 当前数据集的声明；没有该版本记录时返回“未确认”。 */
export function declarationFor(
  map: Record<string, Declaration>, dataset?: Dataset,
): Declaration {
  const key = datasetVersionKey(dataset);
  return (key && map[key]) || EMPTY_DECLARATION;
}

function draftKey(versionKey: string): string {
  return versionKey ? DRAFT_PREFIX + versionKey : '';
}

export function readDraftForVersion(versionKey: string): Record<string, any> | null {
  const key = draftKey(versionKey);
  if (!key) return null;
  const value = readJson<Record<string, any>>(key);
  return value && typeof value === 'object' ? value : null;
}

export function saveDraftForVersion(versionKey: string, config: Record<string, any>): void {
  const key = draftKey(versionKey);
  if (!key) return;
  writeJson(key, config);
}

export function rememberDownload(record: DownloadRecord): void {
  writeJson(DOWNLOAD_KEY, record);
}

export function forgetDownload(): void {
  removeKey(DOWNLOAD_KEY);
}

export function lastDownload(): DownloadRecord | null {
  const value = readJson<DownloadRecord>(DOWNLOAD_KEY);
  if (!value || typeof value.id !== 'string' || !value.id) return null;
  return {
    id: value.id,
    startedAt: Number(value.startedAt) || 0,
    source: value.source,
    identifier: value.identifier,
  };
}
