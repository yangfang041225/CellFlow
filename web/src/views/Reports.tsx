import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Button, Callout, HTMLSelect, Spinner, Tab, Tabs, Tag,
} from '@blueprintjs/core';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { api, Dataset, Meta, Run } from '../services/api';
import { createTracker, isAbortError } from '../services/async';
import { datasetVersionKey } from '../services/context';

function renderMarkdown(text: string, runId: string): string {
  // 服务端已把 figures/ 改写成绝对接口；这里再做一次兜底并严格清洗 HTML。
  const fixed = text
    .replace(/\]\(figures\//g, `](/api/runs/${runId}/figure/`)
    .replace(/src="figures\//g, `src="/api/runs/${runId}/figure/`);
  const html = marked.parse(fixed) as string;
  return DOMPurify.sanitize(html, {
    ALLOWED_URI_REGEXP: /^(?:https?:|mailto:|#|\/api\/)/i,
    FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'form'],
    FORBID_ATTR: ['onerror', 'onload', 'onclick'],
  });
}

export function ReportsView(props: {
  meta: Meta; dataset?: Dataset; notify: (kind: 'ok' | 'warn' | 'bad', text: string) => void;
}) {
  const { meta, dataset, notify } = props;
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState('');
  const [kind, setKind] = useState<'a' | 'b'>('a');
  const [html, setHtml] = useState('');
  const [htmlKey, setHtmlKey] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const runsTracker = useRef(createTracker()).current;
  const reportTracker = useRef(createTracker()).current;
  const contextKey = `${meta.active_project?.id || ''}|${datasetVersionKey(dataset)}`;
  // 报告内容的归属签名：只有与当前“项目+数据+任务+类型”一致时才渲染，避免切上下文瞬间露出旧报告。
  const signature = `${contextKey}|${runId}|${kind}`;

  useEffect(() => {
    const controller = new AbortController();
    const token = runsTracker.begin();
    // F33：服务端分页后 limit 默认 100；报告下拉取上限 200。
    api.runs({ limit: 200 }, controller.signal)
      .then((data) => {
        if (!runsTracker.current(token)) return;
        setRuns(data.runs.filter((run) => run.state === 'completed' || run.state === 'failed'));
      })
      .catch((exc) => { if (runsTracker.current(token) && !isAbortError(exc)) notify('bad', String(exc)); });
    return () => controller.abort();
  }, [meta]);

  // F08：报告与结果页使用同一作用域——只列出当前数据集（且当前项目）的任务。
  const scoped = useMemo(() => {
    if (!dataset) return [] as Run[];
    return runs.filter((run) => (run.dataset || run.input || '') === dataset.path);
  }, [runs, dataset]);

  useEffect(() => {
    if (!scoped.length) {
      setRunId('');
      return;
    }
    setRunId((current) => (current && scoped.some((run) => run.id === current)
      ? current
      : scoped[0].id));
  }, [scoped]);

  // 项目/数据切换时立即清掉已渲染的旧报告（旧图、旧下载入口一起消失）。
  useEffect(() => {
    setHtml('');
    setError('');
  }, [contextKey]);

  useEffect(() => {
    if (!runId) {
      setHtml('');
      setLoading(false);
      setError('');
      return undefined;
    }
    const controller = new AbortController();
    const token = reportTracker.begin();
    setLoading(true);
    setHtml('');
    setError('');
    api.report(runId, kind, controller.signal)
      .then((text) => {
        if (!reportTracker.current(token)) return;
        setHtml(renderMarkdown(text, runId));
        setHtmlKey(signature);
      })
      .catch((exc) => {
        if (!reportTracker.current(token) || isAbortError(exc)) return;
        setHtml('');
        setError(String(exc));
      })
      .finally(() => { if (reportTracker.current(token)) setLoading(false); });
    return () => controller.abort();
  }, [runId, kind]);

  return (
    <div>
      <div className="cf-h1">报告</div>
      <div className="cf-sub">A 生物学分析报告与 B 技术与复现报告，内容来自当前数据集的实际运行产出。</div>

      {!dataset ? (
        <Callout intent="warning">先到「数据」选择一个数据集；报告只来自当前数据集的任务。</Callout>
      ) : scoped.length === 0 ? (
        <Callout intent="warning">当前数据集还没有可导出报告的任务。</Callout>
      ) : null}

      {scoped.length ? (
        <>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', margin: '6px 0' }}>
            <HTMLSelect value={runId} onChange={(event) => setRunId(event.currentTarget.value)}>
              {scoped.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.name || run.id.slice(0, 8)} · {run.state}
                </option>
              ))}
            </HTMLSelect>
            <Tag minimal>当前：{kind === 'a' ? 'A 生物学' : 'B 技术与复现'}</Tag>
            {runId ? (
              <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                <a href={api.zipUrl(runId)} download><Button icon="download">打包 ZIP</Button></a>
                <a href={api.pdfUrl(runId, kind)} download>
                  <Button icon="document">导出 PDF</Button>
                </a>
                <a href={`/api/runs/${runId}/report/${kind}_html`} target="_blank" rel="noreferrer">
                  <Button icon="document-open">打开 HTML</Button>
                </a>
              </div>
            ) : null}
          </div>

          <Tabs id="report-tabs" selectedTabId={kind} onChange={(value) => setKind(value as 'a' | 'b')}>
            <Tab id="a" title="A 生物学分析" />
            <Tab id="b" title="B 技术与复现" />
          </Tabs>

          {loading ? <Spinner size={18} /> : null}
          {error ? <Callout intent="danger" compact>{error}</Callout> : null}
          {!loading && !error && htmlKey === signature ? (
            <div
              style={{
                border: '1px solid #d3d8de', background: '#fff', padding: '14px 18px',
                marginTop: 8, fontSize: 12.6, lineHeight: 1.75, maxHeight: 'calc(100vh - 260px)',
                overflow: 'auto',
              }}
              dangerouslySetInnerHTML={{ __html: html }}
            />
          ) : null}
          <div className="cf-sub" style={{ marginTop: 6 }}>
            报告包含本机绝对路径与样本 ID，外发前请审阅。
          </div>
        </>
      ) : null}
    </div>
  );
}
