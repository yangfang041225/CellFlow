import { useEffect, useMemo, useRef, useState } from 'react';
import { Button, Callout, Dialog, HTMLSelect, InputGroup, Spinner, Tag } from '@blueprintjs/core';
import { api, Dataset, Meta, Run } from '../services/api';
import { createTracker, isAbortError } from '../services/async';
import type { ViewKey } from '../app';

function dur(run: Run): string {
  const start = Number(run.submitted || 0);
  const end = Number(run.updated || 0);
  if (!start || !end || end <= start) return '—';
  const seconds = end - start;
  return seconds < 120 ? `${seconds.toFixed(0)} 秒` : `${(seconds / 60).toFixed(1)} 分`;
}

const STATE_LABEL: Record<string, string> = {
  queued: '排队', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消',
};
const STATE_INTENT: Record<string, 'none' | 'primary' | 'success' | 'warning' | 'danger'> = {
  queued: 'warning', running: 'primary', completed: 'success', failed: 'danger', cancelled: 'none',
};
const ACTIVE = new Set(['queued', 'running']);
const PAGE_SIZE = 50;

export function Overview(props: {
  meta: Meta; dataset?: Dataset; refresh: () => void;
  notify: (kind: 'ok' | 'warn' | 'bad', text: string) => void;
  onNavigate: (view: ViewKey) => void;
  onOpenRun: (run: Run) => void;
}) {
  const { meta, dataset, refresh, notify, onNavigate, onOpenRun } = props;
  const [runs, setRuns] = useState<Run[]>([]);
  const [error, setError] = useState('');
  const [stateFilter, setStateFilter] = useState('');
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(1);
  const [detailOpen, setDetailOpen] = useState(false);
  const [detail, setDetail] = useState<any>(null);
  const [detailError, setDetailError] = useState('');
  const runsTracker = useRef(createTracker()).current;
  const detailTracker = useRef(createTracker()).current;
  const detailAbort = useRef<AbortController | null>(null);

  useEffect(() => () => {
    detailAbort.current?.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const token = runsTracker.begin();
      api.runs({ state: stateFilter, q: query }, controller.signal)
        .then((data) => { if (runsTracker.current(token)) setRuns(data.runs); })
        .catch((exc) => {
          if (runsTracker.current(token) && !isAbortError(exc)) setError(String(exc));
        });
    }, 200);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [meta, stateFilter, query]);

  // 筛选条件变化时回到第 1 页
  useEffect(() => {
    setPage(1);
  }, [stateFilter, query]);

  const pageCount = Math.max(1, Math.ceil(runs.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const pageStart = (currentPage - 1) * PAGE_SIZE;
  const pageRows = runs.slice(pageStart, pageStart + PAGE_SIZE);

  // 任务变少（筛选/删除）导致当前页越界时，收敛到最后一页
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const datasets = useMemo(
    () => meta.datasets.filter((item) => item.source !== '示例'),
    [meta.datasets],
  );
  const counts = meta.counts || {};
  const active = runs.filter((run) => ACTIVE.has(run.state));

  const cancel = async (id: string) => {
    try {
      const result = await api.cancel(id);
      notify('ok', result.message || '已取消');
      refresh();
      setRuns((current) => current.map((item) => (
        item.id === id ? { ...item, state: 'cancelled' } : item
      )));
    } catch (exc) {
      notify('bad', `取消失败：${exc}`);
    }
  };

  return (
    <div>
      <div className="cf-h1">概览</div>
      <div className="cf-sub">项目状态、近期任务与运行队列。任务在后台独立运行。</div>

      <div className="cf-kpi">
        <div className="it"><div className="n">{datasets.length}</div><div className="l">数据集</div></div>
        <div className="it"><div className="n">{counts.completed || 0}</div><div className="l">已完成</div></div>
        <div className="it"><div className="n">{(counts.running || 0) + (counts.queued || 0)}</div><div className="l">进行中</div></div>
        <div className="it"><div className="n">{counts.failed || 0}</div><div className="l">失败</div></div>
      </div>

      {error ? <Callout intent="danger" compact>{error}</Callout> : null}

      <div className="cf-sec">最近任务 <span className="h">按名称检索；只显示本项目的任务</span></div>
      <div style={{ display: 'flex', gap: 6, marginBottom: 6, flexWrap: 'wrap' }}>
        <HTMLSelect value={stateFilter} onChange={(event) => setStateFilter(event.currentTarget.value)}
                    style={{ width: 120, height: 28 }}>
          <option value="">全部状态</option>
          <option value="queued">排队</option>
          <option value="running">运行中</option>
          <option value="completed">已完成</option>
          <option value="failed">失败</option>
          <option value="cancelled">已取消</option>
        </HTMLSelect>
        <div style={{ flex: 1, minWidth: 220 }}>
          <InputGroup leftIcon="search" placeholder="按任务名称或 ID 搜索"
                      value={query} onChange={(event) => setQuery(event.currentTarget.value)} />
        </div>
        <Button icon="refresh" onClick={refresh}>刷新</Button>
      </div>

      {runs.length === 0 ? (
        <Callout compact>没有匹配的任务。到「模型分析」运行一个方法。</Callout>
      ) : (
        <>
        <div style={{ maxHeight: 320, overflow: 'auto', border: '1px solid #e7eaee' }}>
          <table className="cf-table"
                 style={{ width: '100%', minWidth: 660, borderCollapse: 'collapse', fontSize: 12.4 }}>
            <thead style={{ position: 'sticky', top: 0, background: '#fafbfc' }}>
              <tr>
                <th style={{ textAlign: 'left', padding: '5px 8px' }}>任务名称</th>
                <th style={{ textAlign: 'left', padding: '5px 8px' }}>方法</th>
                <th style={{ textAlign: 'left', padding: '5px 8px' }}>状态</th>
                <th style={{ textAlign: 'left', padding: '5px 8px' }}>提交</th>
                <th style={{ textAlign: 'left', padding: '5px 8px' }}>耗时</th>
                <th style={{ textAlign: 'left', padding: '5px 8px' }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((run) => (
                <tr key={run.id} style={{ borderTop: '1px solid #eef1f4' }}>
                  <td style={{ padding: '5px 8px' }}>
                    {run.name || run.id.slice(0, 8)}
                    <div className="cf-sub" style={{ fontSize: 10.8 }}>{run.id.slice(0, 8)}</div>
                  </td>
                  <td style={{ padding: '5px 8px' }}>{run.method_label}</td>
                  <td style={{ padding: '5px 8px' }}>
                    <Tag minimal intent={STATE_INTENT[run.state] || 'none'}>
                      {STATE_LABEL[run.state] || run.state}
                    </Tag>
                  </td>
                  <td style={{ padding: '5px 8px', whiteSpace: 'nowrap' }}>{new Date((run.submitted || 0) * 1000).toLocaleString('zh-CN')}</td>
                  <td style={{ padding: '5px 8px', whiteSpace: 'nowrap' }}>{dur(run)}</td>
                  <td style={{ padding: '5px 8px', whiteSpace: 'nowrap' }}>
                    {ACTIVE.has(run.state) ? (
                      <Button small minimal intent="danger" onClick={() => cancel(run.id)}>取消</Button>
                    ) : (
                      <Button small minimal onClick={() => onOpenRun(run)}>查看</Button>
                    )}
                    <Button small minimal icon="edit" onClick={async () => {
                      const next = window.prompt('任务名称', run.name || '');
                      if (!next || !next.trim()) return;
                      try {
                        await api.renameRun(run.id, next.trim());
                        setRuns((current) => current.map((item) => (
                          item.id === run.id ? { ...item, name: next.trim() } : item
                        )));
                        notify('ok', '已重命名');
                      } catch (exc) {
                        notify('bad', `重命名失败：${exc}`);
                      }
                    }} />
                    <Button small minimal icon="trash" onClick={async () => {
                      if (!window.confirm(`删除任务记录？\n${run.name || run.id}\n\n结果文件会保留，仅删除记录。`)) return;
                      try {
                        await api.deleteRun(run.id);
                        setRuns((current) => current.filter((item) => item.id !== run.id));
                        notify('ok', '已删除任务记录');
                      } catch (exc) {
                        notify('bad', `删除失败：${exc}`);
                      }
                    }} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{
          display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginTop: 6,
        }}>
          <span className="cf-sub" style={{ fontVariantNumeric: 'tabular-nums' }}>
            共 {runs.length} 条 · 当前显示第 {pageStart + 1}–{Math.min(pageStart + PAGE_SIZE, runs.length)} 条
          </span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <Button small minimal icon="chevron-left" disabled={currentPage <= 1}
                    onClick={() => setPage(currentPage - 1)}>上一页</Button>
            <span className="cf-mono" style={{ fontVariantNumeric: 'tabular-nums' }}>
              第 {currentPage} / {pageCount} 页
            </span>
            <Button small minimal endIcon="chevron-right" disabled={currentPage >= pageCount}
                    onClick={() => setPage(currentPage + 1)}>下一页</Button>
          </div>
        </div>
        </>
      )}

      <div className="cf-sec">数据集 <span className="h">当前：{dataset?.label || '未选择'}</span></div>
      {datasets.length === 0 ? (
        <Callout compact>还没有导入数据集。到「数据」上传 .h5ad / loom / 10x 文件。</Callout>
      ) : (
        <div style={{ maxHeight: 180, overflow: 'auto', border: '1px solid #e7eaee' }}>
          <table className="cf-table"
                 style={{ width: '100%', minWidth: 560, borderCollapse: 'collapse', fontSize: 12.4 }}>
            <thead style={{ position: 'sticky', top: 0, background: '#fafbfc' }}>
              <tr>
                <th style={{ textAlign: 'left', padding: '5px 8px' }}>名称</th>
                <th style={{ textAlign: 'left', padding: '5px 8px' }}>来源</th>
                <th style={{ textAlign: 'left', padding: '5px 8px' }}>格式</th>
                <th style={{ textAlign: 'left', padding: '5px 8px' }}>大小</th>
                <th style={{ textAlign: 'left', padding: '5px 8px' }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {datasets.map((item) => (
                <tr key={item.id} style={{ borderTop: '1px solid #eef1f4' }}>
                  <td style={{ padding: '5px 8px' }}>
                    {item.label}
                    {item.id === dataset?.id ? <Tag minimal style={{ marginLeft: 6 }}>当前</Tag> : null}
                  </td>
                  <td style={{ padding: '5px 8px' }}>{item.source}</td>
                  <td style={{ padding: '5px 8px' }}>{item.kind}</td>
                  <td style={{ padding: '5px 8px' }}>{item.size}</td>
                  <td style={{ padding: '5px 8px', whiteSpace: 'nowrap' }}>
                    <Button small minimal icon="info-sign" onClick={async () => {
                      setDetailOpen(true);
                      setDetail(null);
                      setDetailError('');
                      const token = detailTracker.begin();
                      detailAbort.current?.abort();
                      const controller = new AbortController();
                      detailAbort.current = controller;
                      try {
                        const data = await api.datasetDetail(item.id, controller.signal);
                        if (detailTracker.current(token)) setDetail(data);
                      } catch (exc) {
                        if (detailTracker.current(token) && !isAbortError(exc)) {
                          setDetailError(String(exc));
                        }
                      }
                    }} />
                    <Button small minimal icon="trash" onClick={async () => {
                      if (!window.confirm(`移除数据集登记？\n${item.label}\n\n只移除登记，不删除文件；被任务引用的数据集会被拒绝。`)) return;
                      try {
                        const result = await api.removeDataset(item.id);
                        notify('ok', result.message || '已移除登记');
                        refresh();
                      } catch (exc) {
                        notify('bad', `${exc}`);
                      }
                    }} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="cf-sec">下一步</div>
      {datasets.length === 0 ? (
        <Callout intent="primary" compact>进入「数据」，上传或选择本机数据文件。</Callout>
      ) : runs.length === 0 ? (
        <Callout intent="primary" compact>数据已就绪。进入「模型分析」，确认前置条件后运行一个方法。</Callout>
      ) : active.length ? (
        <Callout intent="warning" compact>
          有 {active.length} 个任务在运行或排队。离开窗口不会中断；不要重复提交相同配置（会复用）。
        </Callout>
      ) : (
        <Callout intent="success" compact>到「分析结果」检查图与指标，再到「报告」导出 A/B。</Callout>
      )}

      <div style={{ marginTop: 8, display: 'flex', gap: 6 }}>
        <Button intent="primary" onClick={() => onNavigate('data')}>导入数据</Button>
        <Button onClick={() => onNavigate('model')}>运行模型</Button>
        <Button onClick={() => onNavigate('results')} disabled={!runs.some((run) => run.state === 'completed')}>
          查看结果
        </Button>
      </div>

      <Dialog isOpen={detailOpen} onClose={() => setDetailOpen(false)} title="数据集详情" style={{ width: 560 }}>
        <div style={{ padding: 12, fontSize: 12.4 }}>
          {detailError ? <Callout intent="danger" compact>{detailError}</Callout> : null}
          {!detail && !detailError ? <Spinner size={16} /> : null}
          {detail ? (
            <>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <tbody>
                  <tr><td style={{ padding: 3, color: '#566069' }}>名称</td><td>{detail.label}</td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>来源</td><td>{detail.source} · {detail.kind}</td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>维度</td><td>{detail.n_cells} 细胞 × {detail.n_genes} 基因</td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>X 状态</td><td>{detail.x_kind || '未知'}</td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>层</td><td>{detail.layers.join('、') || '无'}</td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>obs 列</td><td>{detail.obs.join('、') || '无'}</td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>大小</td><td>{detail.size}</td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>
                    {detail.hash_kind === 'sha256' ? 'SHA-256' : '内容指纹'}</td>
                    <td className="cf-mono" style={{ fontSize: 10.8, wordBreak: 'break-all' }}>
                      {detail.content_hash}
                      {detail.hash_kind !== 'sha256'
                        ? <div style={{ fontSize: 10.8, color: '#a8541f' }}>大文件仅取首尾 4 MB + 大小，不能当作完整 SHA-256</div>
                        : null}
                    </td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>内存预估</td>
                    <td>{detail.memory ? (detail.memory.gb + ' GB（可用 ' + detail.memory.available_gb + ' GB；' + detail.memory.method + '）') : '—'}</td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>表达范围</td>
                    <td>{detail.preview ? (detail.preview.stats.min + ' – ' + detail.preview.stats.max + '（非零 ' + detail.preview.stats.nonzero_pct + '%，' + (detail.preview.stats.sparse ? '稀疏' : '稠密') + '）') : '—'}</td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>前几个基因</td>
                    <td className="cf-mono" style={{ fontSize: 11 }}>{detail.preview ? detail.preview.genes.slice(0, 5).join('、') : '—'}</td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>obs 摘要</td>
                    <td>{detail.preview && detail.preview.obs_summary ? Object.entries(detail.preview.obs_summary).slice(0, 3).map(([key, value]: [string, any]) => (
                      <div key={key}>{key}：{value.unique <= 20 ? value.top.slice(0, 3).map((item: any) => item.zh ? item.value + '（' + item.zh + '）' : item.value).join(' / ') : (value.unique + ' 个不同值（连续或高基数）')}</div>
                    )) : '—'}\n                    <div className="cf-sub" style={{ fontSize: 10.8 }}>中文为参考翻译；未收录术语保持原文。</div></td></tr>
                  <tr><td style={{ padding: 3, color: '#566069' }}>引用任务</td>
                    <td>{detail.task_count} 个{detail.tasks.length ? `（如 ${detail.tasks[0].name}）` : ''}</td></tr>
                </tbody>
              </table>
              {detail.warnings.length ? (
                <Callout intent="warning" compact style={{ marginTop: 6 }}>
                  {detail.warnings.slice(0, 3).map((item: string) => <div key={item}>{item}</div>)}
                </Callout>
              ) : null}
              <div className="cf-sub" style={{ marginTop: 6 }}>
                移除登记不会删除文件；被任务引用时会被拒绝。
              </div>
            </>
          ) : null}
        </div>
      </Dialog>
    </div>
  );
}
