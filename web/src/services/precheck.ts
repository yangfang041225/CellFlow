import type { Inspect, MethodRow } from './api';
import { AUTO_GROUP } from './context';

/**
 * 模型准入预检。
 *
 * 后端 /api/datasets/inspect 只接受 path / raw_x / raw_su，服务端在计算准入时
 * 拿不到模型页表单里的 group_key、root_cell、root_reason、grn_path、grn_confirmed、
 * grn_source，因此 DPT/CellRank 的 root、PAGA 的 groups、RegVelo 的 prior_GRN
 * 永远被判为缺失（按钮一直禁用）。在不能改后端的约束下，这里按后端 registry.py
 * 的同一套规则，用 inspect 返回的 facts + 本地表单值复算这几项，并且：
 *   - 按钮的 disabled 与提交前的拦截使用同一份 MethodGate 结论；
 *   - 每次表单字段变化都会重新计算（纯同步、无请求，所以不存在旧响应覆盖）。
 * 服务端仍然无法在本机核实“起点细胞 ID 是否存在”，提交后运行器会再校验一次。
 */

export type MethodGate = {
  key: string;
  name: string;
  family: string;
  status: string;
  /** 前置条件与运行时依赖都满足，允许提交。 */
  ready: boolean;
  /** 还缺的科学前提（界面直接展示给用户）。 */
  missing: string[];
  /** 运行时依赖与数据错误（缺包、适配器未实现、数据本身不合格）。 */
  blockers: string[];
  /** 服务端（空配置）给出的原始结论，便于排查。 */
  serverCanRun: boolean;
};

/** 与后端 cellflow/registry.py missing_needs() 的说明文案保持一致。 */
const NEED_MISSING: Record<string, string> = {
  synthetic: '需要内置模拟数据',
  raw_X: 'X 需确认为未归一化计数（当前未确认，或看起来已经归一化/取过对数）',
  raw_SU: '需要 spliced/unspliced 原始计数层（普通表达矩阵无法补出剪接层）',
  groups: '需要一个细胞分组列，或改用“自动分群”产生群标签',
  root: '需要数据中存在的起始细胞 ID，并填写选择它的生物学依据',
  prior_GRN: '需要先验 GRN CSV，并勾选已核查来源/物种/基因 ID',
  pseudotime: '需要先得到拟时序（例如先做 DPT）',
  matched_ATAC: '需要同一批细胞的 ATAC 可达性矩阵与峰到基因映射',
  protocol: '需要明确是常规剪接数据还是代谢标记数据（含实验时间）',
};

/** 只有这些前提依赖模型页表单，服务端收不到，必须在前端复算。 */
const CONFIG_NEEDS = new Set(['groups', 'root', 'prior_GRN', 'pseudotime']);

/** 关键词：服务端措辞即使有微调，也能识别同一条缺失说明，避免重复/误判。 */
const NEED_MARKERS: Record<string, string[]> = {
  synthetic: ['模拟数据'],
  raw_X: ['未归一化计数'],
  raw_SU: ['spliced/unspliced', '原始计数层'],
  groups: ['分组列'],
  root: ['起始细胞'],
  prior_GRN: ['先验 GRN'],
  pseudotime: ['拟时序'],
  matched_ATAC: ['ATAC'],
  protocol: ['代谢标记'],
};

function trim(value: unknown): string {
  return String(value ?? '').trim();
}

function needsMessage(message: string, need: string): boolean {
  if (message === NEED_MISSING[need]) return true;
  return (NEED_MARKERS[need] || []).some((marker) => message.includes(marker));
}

/**
 * 复算一条前提是否满足。
 * 返回 null 表示“前端无法判定”，保留服务端的 missing 结论（保守，不放行）。
 */
function decide(
  need: string, facts: Inspect['facts'] | undefined, config: Record<string, any>,
): boolean | null {
  switch (need) {
    case 'groups': {
      const key = trim(config.group_key);
      if (!key) return false;
      if (key === AUTO_GROUP) return true;
      const columns = facts?.obs_keys || [];
      if (!columns.length) return null;
      return columns.includes(key);
    }
    case 'root':
      // inspect 不返回细胞 ID 列表，这里只核对“已填起点 + 已填生物学依据”；
      // 起点是否真的存在于数据中，由提交后的运行器数据校验确认。
      return Boolean(trim(config.root_cell) && trim(config.root_reason));
    case 'prior_GRN':
      return Boolean(trim(config.grn_path) && trim(config.grn_source) && config.grn_confirmed === true);
    case 'pseudotime':
      return Boolean(facts?.velocity_counts || trim(config.root_cell));
    default:
      return null;
  }
}

/**
 * 把服务端 MethodRow 与本地配置复算合并成一份准入结论。
 * 模型页的按钮状态与提交前拦截都只读这份结果（F06：同一份校验结果）。
 */
export function evaluateMethods(
  rows: MethodRow[], facts: Inspect['facts'] | undefined, config: Record<string, any>,
): MethodGate[] {
  const allNeeds = Object.keys(NEED_MARKERS);
  return (rows || []).map((row) => {
    const missing: string[] = [];
    const localNeeds: string[] = [];
    for (const need of row.needs || []) {
      if (!CONFIG_NEEDS.has(need)) continue;
      const decision = decide(need, facts, config);
      if (decision === null) continue; // 交给服务端结论
      localNeeds.push(need);
      if (!decision) missing.push(NEED_MISSING[need] || need);
    }
    // 服务端按“空配置”算出来的本地前提说明不再采信（已在上方复算）。
    const otherMissing = (row.missing || []).filter(
      (message) => !localNeeds.some((need) => needsMessage(message, need)),
    );
    // blockers 里混着 missing_needs 的说明、运行时依赖与数据错误；先剔掉前提说明，
    // 前提由上面的 missing 列表统一表达（含 raw_X / raw_SU 等数据侧前提）。
    const blockers = (row.blockers || []).filter(
      (message) => !allNeeds.some((need) => needsMessage(message, need)),
    );
    return {
      key: row.key,
      name: row.name,
      family: row.family,
      status: row.status,
      ready: blockers.length === 0 && otherMissing.length === 0 && missing.length === 0,
      missing: [...otherMissing, ...missing],
      blockers,
      serverCanRun: Boolean(row.can_run),
    };
  });
}

export function gateFor(gates: MethodGate[], key: string): MethodGate | undefined {
  return gates.find((item) => item.key === key);
}
