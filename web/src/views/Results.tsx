import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import {
  Button, Callout, Dialog, HTMLSelect, HTMLTable, InputGroup, Spinner, Tag,
} from '@blueprintjs/core';
import { DockviewReact, DockviewReadyEvent, IDockviewPanelProps, themeLight } from 'dockview-react';
import { api, Dataset, Meta, Run, RunDetail } from '../services/api';
import { createTracker, isAbortError } from '../services/async';
import { datasetVersionKey } from '../services/context';

const FIG_CAPTIONS: Record<string, string> = {
  'embedding.svg': '二维投影（箭头仅表示模型给出的方向）',
  'qc.svg': '质量检查：总表达 vs 检测基因数（未自动过滤）',
  'phase.svg': '剪接相图（需要 spliced/unspliced）',
  'trends.svg': '候选基因趋势（探索性）',
  'paga.svg': 'PAGA 群间连接（无向强度）',
  'stream.png': '速度流线图（网格插值，仅显示走向）',
  'fate.svg': '命运概率（模型推断）',
};

const RunContext = createContext<RunDetail | null>(null);
// F31：运行中任务的日志按 tick / updated 刷新，不再只依赖 id 与 state。
const TickContext = createContext(0);

function FiguresPanel(_props: IDockviewPanelProps) {
  const run = useContext(RunContext);
  if (!run) return <div className="cf-panel-pad">没有结果</div>;
  if (!run.figures.length) {
    return <div className="cf-panel-pad"><Callout compact>这份任务没有图形文件。</Callout></div>;
  }
  return (
    <div className="cf-panel-pad">
      {run.figures.map((name) => (
        <div className="cf-fig" key={name} style={{ marginBottom: 8 }}>
          <img src={api.figureUrl(run.id, name)} alt={name} />
          <div className="cap">{FIG_CAPTIONS[name] || name} <span className="cf-mono">figures/{name}</span></div>
        </div>
      ))}
    </div>
  );
}

function MetricsPanel(_props: IDockviewPanelProps) {
  const run = useContext(RunContext);
  const metrics = run?.result?.metrics || {};
  const rows = Object.entries(metrics);
  return (
    <div className="cf-panel-pad">
      {rows.length === 0 ? <Callout compact>该方法没有输出数值指标。</Callout> : (
        <HTMLTable compact striped>
          <thead><tr><th>指标</th><th>值</th></tr></thead>
          <tbody>
            {rows.map(([key, value]) => (
              <tr key={key}>
                <td className="cf-mono">{key}</td>
                <td>{typeof value === 'number' ? Number(value.toPrecision(5)) : String(value)}</td>
              </tr>
            ))}
          </tbody>
        </HTMLTable>
      )}
    </div>
  );
}

const GENE_MATCH_LIMIT = 200;
const GENE_FLAT_LIMIT = 40;

function GeneSearch(props: { value: string; onChange: (value: string) => void; summary: string }) {
  return (
    <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginBottom: 6 }}>
      <div style={{ flex: 1, minWidth: 170 }}>
        <InputGroup
          leftIcon="search"
          placeholder="按基因名筛选（不区分大小写）"
          value={props.value}
          onChange={(event) => props.onChange(event.currentTarget.value)}
          rightElement={props.value ? (
            <Button minimal small icon="cross" aria-label="清除筛选" onClick={() => props.onChange('')} />
          ) : undefined}
        />
      </div>
      <span className="cf-sub" style={{ whiteSpace: 'nowrap' }}>{props.summary}</span>
    </div>
  );
}

function GeneEmpty(props: { query: string; onClear: () => void }) {
  return (
    <div className="cf-empty" style={{ marginBottom: 8 }}>
      没有基因名包含「{props.query}」的候选基因。
      <div style={{ marginTop: 6 }}>
        <Button small minimal onClick={props.onClear}>清除筛选</Button>
      </div>
    </div>
  );
}

function GenesPanel(_props: IDockviewPanelProps) {
  const run = useContext(RunContext);
  const table: any[] = run?.result?.gene_table || [];
  const [geneQuery, setGeneQuery] = useState('');

  // 切换任务时清掉上一个任务的筛选词
  useEffect(() => {
    setGeneQuery('');
  }, [run?.id]);

  const query = geneQuery.trim().toLowerCase();
  const clearQuery = () => setGeneQuery('');

  if (!table.length) return <div className="cf-panel-pad"><Callout compact>没有候选基因表。</Callout></div>;

  // auto 的 gene_table 是按群分组的结构：[{group, n, genes:[{gene, diff, mean_in}]}]
  if (Array.isArray(table[0]?.genes)) {
    const groups = table.map((group: any, index: number) => {
      const genes: any[] = Array.isArray(group?.genes) ? group.genes : [];
      const matched = query
        ? genes.filter((row) => String(row?.gene ?? '').toLowerCase().includes(query))
        : genes;
      return { group, index, genes, matched };
    });
    const totalGenes = groups.reduce((sum, item) => sum + item.genes.length, 0);
    const matchCount = groups.reduce((sum, item) => sum + item.matched.length, 0);
    const perGroup = query ? GENE_MATCH_LIMIT : 10;
    const visible = groups
      .filter((item) => !query || item.matched.length > 0)
      .slice(0, query ? 30 : 8)
      .map((item) => ({ ...item, rows: item.matched.slice(0, perGroup) }));
    const summary = query
      ? `匹配 ${matchCount} 条 / 共 ${totalGenes} 条`
      : `共 ${totalGenes} 条候选基因`;

    return (
      <div className="cf-panel-pad">
        <GeneSearch value={geneQuery} onChange={setGeneQuery} summary={summary} />
        {query && !matchCount ? <GeneEmpty query={geneQuery.trim()} onClear={clearQuery} /> : null}
        {visible.map((item) => (
          <div key={item.index} style={{ marginBottom: 10 }}>
            <div className="cf-sub" style={{ marginBottom: 2 }}>
              群 {String(item.group.group)} · {item.group.n ?? item.genes.length} 个细胞
              {query && item.matched.length > item.rows.length
                ? ` · 仅显示前 ${item.rows.length} 条匹配` : ''}
            </div>
            <div style={{ overflowX: 'auto' }}>
              <HTMLTable compact striped style={{ minWidth: 300 }}>
                <thead><tr><th>基因</th><th>群内差值</th><th>群内均值</th></tr></thead>
                <tbody>
                  {item.rows.map((row: any) => (
                    <tr key={row.gene}>
                      <td className="cf-mono">{String(row.gene)}</td>
                      <td>{typeof row.diff === 'number' ? Number(row.diff.toPrecision(4)) : '—'}</td>
                      <td>{typeof row.mean_in === 'number' ? Number(row.mean_in.toPrecision(4)) : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </HTMLTable>
            </div>
          </div>
        ))}
        {query && matchCount > 0 && groups.length > visible.length ? (
          <div className="cf-sub" style={{ marginBottom: 6 }}>
            匹配的群较多，仅显示前 {visible.length} 个群；可用更具体的基因名缩小范围。
          </div>
        ) : null}
        <div className="cf-sub">群内相对高表达基因，属描述性结果；不是标记基因，也不证明因果。</div>
      </div>
    );
  }

  // 扁平结构：[{gene, ..., ...}]，按速度等指标排序
  const allKeys = Object.keys(table[0] || {});
  const keys = allKeys.slice(0, 4);
  const geneKey = allKeys.find((key) => key.toLowerCase() === 'gene')
    || allKeys.find((key) => key.toLowerCase().includes('gene'))
    || allKeys[0];
  const matchedRows = query
    ? table.filter((row: any) => String(row?.[geneKey] ?? '').toLowerCase().includes(query))
    : table;
  const shown = matchedRows.slice(0, GENE_FLAT_LIMIT);
  const summary = query
    ? `匹配 ${matchedRows.length} 条 / 共 ${table.length} 条`
    : `共 ${table.length} 条候选基因`;

  return (
    <div className="cf-panel-pad">
      <GeneSearch value={geneQuery} onChange={setGeneQuery} summary={summary} />
      {query && !matchedRows.length ? (
        <GeneEmpty query={geneQuery.trim()} onClear={clearQuery} />
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <HTMLTable compact striped style={{ minWidth: 340 }}>
            <thead><tr>{keys.map((key) => <th key={key}>{key}</th>)}</tr></thead>
            <tbody>
              {shown.map((row: any, index: number) => (
                <tr key={index}>{keys.map((key) => (
                  <td key={key}>{typeof row[key] === 'number' ? Number(Number(row[key]).toPrecision(5)) : String(row[key])}</td>
                ))}</tr>
              ))}
            </tbody>
          </HTMLTable>
        </div>
      )}
      <div className="cf-sub" style={{ marginTop: 4 }}>
        按速度绝对值排序，属描述性结果，不证明因果。
        {query && matchedRows.length > shown.length
          ? `（匹配 ${matchedRows.length} 条，仅显示前 ${shown.length} 条）` : ''}
      </div>
    </div>
  );
}

function LogPanel(_props: IDockviewPanelProps) {
  const [log, setLog] = useState('');
  const run = useContext(RunContext);
  const tick = useContext(TickContext);
  useEffect(() => {
    if (!run) {
      setLog('');
      return undefined;
    }
    let alive = true;
    const controller = new AbortController();
    api.log(run.id, controller.signal)
      .then((data) => { if (alive) setLog(data.log); })
      .catch((exc) => { if (alive && !isAbortError(exc)) setLog(''); });
    return () => { alive = false; controller.abort(); };
    // updated 随运行阶段推进而变化：运行中也能持续刷新日志
  }, [run?.id, run?.state, run?.updated, tick]);
  return (
    <div className="cf-panel-pad">
      <pre style={{ fontSize: 11.4, whiteSpace: 'pre-wrap', margin: 0 }}>{log || '（没有 run.log）'}</pre>
    </div>
  );
}

const dockComponents = {
  figures: FiguresPanel, metrics: MetricsPanel, genes: GenesPanel, log: LogPanel,
};

export function ResultsView(props: {
  meta: Meta; dataset?: Dataset; notify: (kind: 'ok' | 'warn' | 'bad', text: string) => void;
  tick?: number; focusRunId?: string; onFocusConsumed?: () => void;
}) {
  const { meta, notify, tick, dataset, focusRunId, onFocusConsumed } = props;
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState('');
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [compareOpen, setCompareOpen] = useState(false);
  const [compareTarget, setCompareTarget] = useState('');
  const [compareData, setCompareData] = useState<{
    changes: { key: string; a: unknown; b: unknown }[];
    same: boolean;
    metric_changes?: { key: string; a: unknown; b: unknown }[];
    figures?: { a: string[]; b: string[] };
    can_compare_figures?: boolean;
    compare_reason?: string;
  } | null>(null);
  const [compareError, setCompareError] = useState('');
  const runsTracker = useRef(createTracker()).current;
  const detailTracker = useRef(createTracker()).current;
  const compareTracker = useRef(createTracker()).current;

  // 当前上下文（项目 + 数据版本）：切换后旧结果必须立刻消失。
  const contextKey = `${meta.active_project?.id || ''}|${datasetVersionKey(dataset)}`;

  useEffect(() => {
    const controller = new AbortController();
    const token = runsTracker.begin();
    api.runs(undefined, controller.signal)
      .then((data) => { if (runsTracker.current(token)) setRuns(data.runs); })
      .catch((exc) => { if (runsTracker.current(token) && !isAbortError(exc)) notify('bad', String(exc)); });
    return () => controller.abort();
  }, [meta, tick]);

  // 只显示当前数据集（且属于当前项目）的任务；没有当前数据时不再回退到别的数据集。
  const scoped = useMemo(() => {
    if (!dataset) return [] as Run[];
    return runs.filter((run) => {
      const owner = run.dataset || run.input || '';
      return owner === dataset.path;
    });
  }, [runs, dataset]);

  useEffect(() => {
    setCompareOpen(false);
    setCompareTarget('');
    setCompareData(null);
    setCompareError('');
  }, [contextKey]);

  useEffect(() => {
    if (!scoped.length) {
      setRunId('');
      return;
    }
    setRunId((current) => (current && scoped.some((run) => run.id === current)
      ? current
      : scoped[0].id));
  }, [scoped]);

  // 概览「查看」：定位到指定任务（等任务列表到达后再选中）。
  useEffect(() => {
    if (!focusRunId) return;
    if (!scoped.some((run) => run.id === focusRunId)) return;
    setRunId(focusRunId);
    onFocusConsumed?.();
  }, [focusRunId, scoped, onFocusConsumed]);

  useEffect(() => {
    if (!compareOpen || !runId || !compareTarget) return undefined;
    const controller = new AbortController();
    const token = compareTracker.begin();
    setCompareError('');
    setCompareData(null);
    api.compareRuns(runId, compareTarget, controller.signal)
      .then((data) => { if (compareTracker.current(token)) setCompareData(data); })
      .catch((exc) => {
        if (!compareTracker.current(token) || isAbortError(exc)) return;
        setCompareData(null);
        setCompareError(String(exc));
      });
    return () => controller.abort();
  }, [compareOpen, runId, compareTarget]);

  const current = scoped.find((run) => run.id === runId);
  // 详情只在 id 与当前选择一致时展示：即使旧响应/旧状态残留，也不会把别的任务的图带出来。
  const shown = detail && detail.id === runId ? detail : null;

  useEffect(() => {
    if (!runId) {
      setDetail(null);
      setLoading(false);
      return undefined;
    }
    const controller = new AbortController();
    const token = detailTracker.begin();
    // 同一任务刷新时保留内容避免闪烁；切换任务时立即清空图、指标与下载入口。
    setDetail((previous) => (previous && previous.id === runId ? previous : null));
    setLoading(true);
    api.run(runId, controller.signal)
      .then((data) => {
        if (!detailTracker.current(token) || data.id !== runId) return;
        setDetail(data);
        setRuns((items) => items.map((item) => (item.id === data.id
          ? { ...item, state: data.state, updated: data.updated, name: data.name || item.name }
          : item)));
      })
      .catch((exc) => {
        if (!detailTracker.current(token) || isAbortError(exc)) return;
        setDetail(null);
        notify('bad', `读取任务失败：${exc}`);
      })
      .finally(() => { if (detailTracker.current(token)) setLoading(false); });
    return () => controller.abort();
  }, [runId, tick]);

  // 运行中自动刷新详情（不显示伪造百分比，只更新真实状态）
  const shownState = shown?.state || '';
  useEffect(() => {
    const id = shown?.id || '';
    if (!id) return undefined;
    if (shownState !== 'queued' && shownState !== 'running') return undefined;
    const timer = window.setInterval(() => {
      api.run(id).then((data) => {
        if (data.id !== id) return;
        setDetail((previous) => (previous && previous.id === id ? data : previous));
        setRuns((items) => items.map((item) => (item.id === data.id
          ? { ...item, state: data.state, updated: data.updated }
          : item)));
      }).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [shown?.id, shownState]);

  const onReady = useCallback((event: DockviewReadyEvent) => {
    event.api.onDidLayoutChange(() => {
      try {
        localStorage.setItem('cf.layout', JSON.stringify(event.api.toJSON()));
      } catch {
        /* 存储不可用时忽略 */
      }
    });
    const saved = localStorage.getItem('cf.layout');
    if (saved) {
      try {
        event.api.fromJSON(JSON.parse(saved));
        return;
      } catch {
        localStorage.removeItem('cf.layout');
      }
    }
    event.api.addPanel({ id: 'figures', component: 'figures', title: '图形' });
    event.api.addPanel({
      id: 'metrics', component: 'metrics', title: '指标',
      position: { referencePanel: 'figures', direction: 'right' },
    });
    event.api.addPanel({
      id: 'genes', component: 'genes', title: '候选基因',
      position: { referencePanel: 'metrics', direction: 'below' },
    });
    event.api.addPanel({
      id: 'log', component: 'log', title: '运行记录',
      position: { referencePanel: 'genes', direction: 'below' },
    });
  }, []);

  const missing = shown && shown.state === 'completed' && shown.artifacts
    && !shown.artifacts.result;

  return (
    <div>
      <div className="cf-h1">分析结果</div>
      <div className="cf-sub">图形、指标、候选基因与运行记录；只显示当前数据集的任务实际产生的文件。</div>

      {!dataset ? (
        <Callout intent="warning">先到「数据」选择一个数据集；本页只显示当前数据集的任务。</Callout>
      ) : scoped.length === 0 ? (
        <Callout intent="warning">当前数据集还没有任务。到「模型分析」运行一个方法。</Callout>
      ) : null}

      {scoped.length ? (
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', margin: '6px 0' }}>
          <HTMLSelect value={runId} onChange={(event) => setRunId(event.currentTarget.value)}>
            {scoped.map((run) => (
              <option key={run.id} value={run.id}>
                {run.name || run.id.slice(0, 8)} · {run.state}
              </option>
            ))}
          </HTMLSelect>
          {shown ? (
            <>
              <Tag minimal intent={shown.state === 'completed' ? 'success' : shown.state === 'failed' ? 'danger' : 'primary'}>
                {shown.state}
              </Tag>
              <span className="cf-sub">{shown.name || shown.method_label}</span>
              {shown.state === 'running' && shown.stage ? (
                <span className="cf-sub">阶段：{shown.stage}</span>
              ) : null}
              {shown.error ? <Callout intent="danger" compact>{shown.error}</Callout> : null}
            </>
          ) : null}
          <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {shown && ['queued', 'running'].includes(shown.state) ? (
              <Button intent="danger" minimal onClick={async () => {
                try {
                  const result = await api.cancel(shown.id);
                  notify('ok', result.message || '已取消');
                  setDetail({ ...shown, state: 'cancelled' });
                } catch (exc) {
                  notify('bad', `取消失败：${exc}`);
                }
              }}>取消任务</Button>
            ) : null}
            {current ? (
              <a href={api.zipUrl(current.id)} download>
                <Button icon="download">下载报告 ZIP</Button>
              </a>
            ) : null}
            <Button minimal icon="comparison" disabled={scoped.filter((item) => item.id !== runId).length === 0}
                    onClick={() => {
                      const other = scoped.find((item) => item.id !== runId);
                      setCompareTarget(other ? other.id : '');
                      setCompareData(null);
                      setCompareOpen(true);
                    }}>对比参数</Button>
            <Button minimal icon="refresh" onClick={() => {
              localStorage.removeItem('cf.layout');
              window.location.reload();
            }}>重置布局</Button>
          </div>
        </div>
      ) : null}

      {loading ? <Spinner size={18} /> : null}

      {missing ? (
        <Callout intent="warning" style={{ marginBottom: 6 }}>
          该任务标记为完成，但结果目录缺少 result.json。目录：{shown!.output}
          （可能被移动或删除；日志仍可查看）
        </Callout>
      ) : null}

      {shown?.quality && (shown.quality.warnings?.length || !shown.quality.ok) ? (
        <Callout intent="warning" style={{ marginBottom: 6 }}>
          <b>结果质量检查</b>
          <ul style={{ margin: '4px 0 0 16px', padding: 0 }}>
            {shown.quality.checks.filter((item) => !item.ok).map((item) => (
              <li key={item.check}>{item.check}{item.detail ? `：${item.detail}` : ''}</li>
            ))}
            {shown.quality.warnings.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </Callout>
      ) : null}

      {shown && shown.result ? (
        <RunContext.Provider value={shown}>
          <TickContext.Provider value={tick ?? 0}>
            <div className="cf-dock">
              <DockviewReact components={dockComponents} onReady={onReady} theme={themeLight} />
            </div>
          </TickContext.Provider>
        </RunContext.Provider>
      ) : null}

      {shown && !shown.result && !missing ? (
        <Callout compact intent="primary">
          任务状态为 {shown.state}：
          {shown.state === 'failed' ? '失败原因见错误信息与运行记录。' : '结果尚未生成，页面会自动刷新。'}
        </Callout>
      ) : null}

      <Dialog isOpen={compareOpen} onClose={() => setCompareOpen(false)} title="参数对比" style={{ width: 560 }}>
        <div style={{ padding: 12, fontSize: 12.4 }}>
          <div style={{ marginBottom: 8 }}>
            <HTMLSelect value={compareTarget} fill
                        onChange={(event) => setCompareTarget(event.currentTarget.value)}>
              {scoped.filter((item) => item.id !== runId).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name || item.id.slice(0, 8)} · {item.state}
                </option>
              ))}
            </HTMLSelect>
          </div>
          {compareError ? <Callout intent="danger" compact>{compareError}</Callout> : null}
          {compareData ? (
            compareData.same ? (
              <Callout compact intent="success">两个任务的配置完全相同。</Callout>
            ) : (
              <div style={{ maxHeight: 340, overflowY: 'auto' }}>
                <HTMLTable compact striped style={{ width: '100%' }}>
                  <thead><tr><th>参数</th><th>当前任务</th><th>对比任务</th></tr></thead>
                  <tbody>
                    {compareData.changes.map((item) => (
                      <tr key={item.key}>
                        <td className="cf-mono">{item.key}</td>
                        <td>{typeof item.a === 'object' ? JSON.stringify(item.a) : String(item.a ?? '—')}</td>
                        <td>{typeof item.b === 'object' ? JSON.stringify(item.b) : String(item.b ?? '—')}</td>
                      </tr>
                    ))}
                  </tbody>
                </HTMLTable>
              </div>
            )
          ) : null}

          {compareData?.metric_changes?.length ? (
            <div style={{ marginTop: 10 }}>
              <div className="cf-sub" style={{ marginBottom: 4 }}>指标差异（不同方法的指标不可直接比大小）</div>
              <HTMLTable compact striped style={{ width: '100%' }}>
                <thead><tr><th>指标</th><th>当前任务</th><th>对比任务</th></tr></thead>
                <tbody>
                  {compareData.metric_changes.map((item) => (
                    <tr key={item.key}>
                      <td className="cf-mono">{item.key}</td>
                      <td>{typeof item.a === 'number' ? Number(item.a.toPrecision(5)) : String(item.a ?? '—')}</td>
                      <td>{typeof item.b === 'number' ? Number(item.b.toPrecision(5)) : String(item.b ?? '—')}</td>
                    </tr>
                  ))}
                </tbody>
              </HTMLTable>
            </div>
          ) : null}

          {compareData ? (
            compareData.can_compare_figures ? (
              <div style={{ marginTop: 10 }}>
                <Callout compact intent="success">两次运行的细胞集合与嵌入参数一致，可并排比较嵌入图。</Callout>
                <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
                  <figure style={{ margin: 0, flex: 1 }}>
                    <img src={api.figureUrl(runId, 'embedding.svg')} style={{ width: '100%' }} alt="当前任务嵌入" />
                    <figcaption className="cf-sub">当前任务</figcaption>
                  </figure>
                  <figure style={{ margin: 0, flex: 1 }}>
                    <img src={api.figureUrl(compareTarget, 'embedding.svg')} style={{ width: '100%' }} alt="对比任务嵌入" />
                    <figcaption className="cf-sub">对比任务</figcaption>
                  </figure>
                </div>
              </div>
            ) : (
              <Callout compact intent="warning" style={{ marginTop: 10 }}>
                图像不可直接并排比较：{compareData.compare_reason || '参数或细胞集合不同'}
              </Callout>
            )
          ) : null}
        </div>
      </Dialog>
    </div>
  );
}
