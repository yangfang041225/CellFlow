import { useEffect, useState } from 'react';
import {
  Button, Callout, HTMLSelect, Spinner, Tab, Tabs, Tag,
} from '@blueprintjs/core';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { api, Dataset, Meta, Run } from '../services/api';

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
  const { meta, notify } = props;
  const [runs, setRuns] = useState<Run[]>([]);
  const [runId, setRunId] = useState('');
  const [kind, setKind] = useState<'a' | 'b'>('a');
  const [html, setHtml] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    api.runs().then((data) => {
      const finished = data.runs.filter((run) => run.state === 'completed' || run.state === 'failed');
      setRuns(finished);
      if (finished.length) setRunId((current) => (current && finished.some((run) => run.id === current) ? current : finished[0].id));
    });
  }, [meta]);

  useEffect(() => {
    if (!runId) return;
    let alive = true;
    setLoading(true);
    setError('');
    api.report(runId, kind)
      .then((text) => { if (alive) setHtml(renderMarkdown(text, runId)); })
      .catch((exc) => { if (alive) { setHtml(''); setError(String(exc)); } })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [runId, kind]);

  return (
    <div>
      <div className="cf-h1">报告</div>
      <div className="cf-sub">A 生物学分析报告与 B 技术与复现报告，内容来自实际运行产出。</div>

      {runs.length === 0 ? <Callout intent="warning">还没有可导出报告的任务。</Callout> : null}

      {runs.length ? (
        <>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', margin: '6px 0' }}>
            <HTMLSelect value={runId} onChange={(event) => setRunId(event.currentTarget.value)}>
              {runs.map((run) => (
                <option key={run.id} value={run.id}>
                  {run.name || run.id.slice(0, 8)} · {run.state}
                </option>
              ))}
            </HTMLSelect>
            <Tag minimal>当前：{kind === 'a' ? 'A 生物学' : 'B 技术与复现'}</Tag>
            <div style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
              <a href={api.zipUrl(runId)} download><Button icon="download">打包 ZIP</Button></a>
              <a href={api.pdfUrl(runId, kind)} download>
                <Button icon="document">导出 PDF</Button>
              </a>
              <a href={`/api/runs/${runId}/report/${kind}_html`} target="_blank" rel="noreferrer">
                <Button icon="document-open">打开 HTML</Button>
              </a>
            </div>
          </div>

          <Tabs id="report-tabs" selectedTabId={kind} onChange={(value) => setKind(value as 'a' | 'b')}>
            <Tab id="a" title="A 生物学分析" />
            <Tab id="b" title="B 技术与复现" />
          </Tabs>

          {loading ? <Spinner size={18} /> : null}
          {error ? <Callout intent="danger" compact>{error}</Callout> : null}
          {!loading && !error ? (
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
