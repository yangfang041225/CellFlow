import { useEffect, useRef, useState } from 'react';
import {
  Button, Callout, FormGroup, HTMLSelect, HTMLTable, InputGroup, ProgressBar, Spinner, Switch, Tag,
} from '@blueprintjs/core';
import { api, Dataset, Inspect, Meta } from '../services/api';

export function DataView(props: {
  meta: Meta; dataset?: Dataset; onSelect: (id: string) => void;
  notify: (kind: 'ok' | 'warn' | 'bad', text: string) => void; refresh: () => void;
}) {
  const { meta, dataset, onSelect, notify, refresh } = props;
  const [inspect, setInspect] = useState<Inspect | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [source, setSource] = useState('current');
  const [localPath, setLocalPath] = useState('');
  const [publicId, setPublicId] = useState('');
  const [publicSource, setPublicSource] = useState<'geo' | 'cellxgene'>('geo');
  const [download, setDownload] = useState<any>(null);
  const pollRef = useRef<number | null>(null);
  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);
  const fileRef = useRef<HTMLInputElement>(null);
  const [rawX, setRawX] = useState(localStorage.getItem('cf.raw_x') === '1');
  const [rawSU, setRawSU] = useState(localStorage.getItem('cf.raw_su') === '1');

  useEffect(() => {
    if (!dataset) { setInspect(null); return; }
    setLoading(true);
    setError('');
    api.inspect(dataset.path, rawX, rawSU)
      .then((data) => setInspect(data))
      .catch((exc) => setError(String(exc)))
      .finally(() => setLoading(false));
  }, [dataset?.path, rawX, rawSU]);

  useEffect(() => {
    localStorage.setItem('cf.raw_x', rawX ? '1' : '0');
    localStorage.setItem('cf.raw_su', rawSU ? '1' : '0');
  }, [rawX, rawSU]);

  const upload = async (file: File) => {
    try {
      const result = await api.upload(file);
      notify('ok', `已导入：${result.path}`);
      refresh();
      onSelect(result.id);
    } catch (exc) {
      notify('bad', `导入失败：${exc}`);
    }
  };

  const useLocal = () => {
    const match = meta.datasets.find((item) => item.path === localPath.trim());
    if (!match) {
      notify('warn', '路径不在项目数据目录内，或尚未登记。请用上传，或先把文件放进 data/public。');
      return;
    }
    onSelect(match.id);
  };

  const stopPolling = () => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const usePublic = async () => {
    stopPolling();
    try {
      const started = await api.startDownload(publicSource, publicId);
      setDownload({ id: started.id, state: 'queued', done: 0, total: 0, message: '排队中' });
      notify('ok', '下载已在后台开始，可以继续用其他页面');
      pollRef.current = window.setInterval(async () => {
        try {
          const status = await api.downloadStatus(started.id);
          setDownload(status);
          if (['done', 'failed', 'cancelled'].includes(status.state)) {
            stopPolling();
            if (status.state === 'done') {
              refresh();
              notify('ok', `下载完成：${status.path}`);
            } else if (status.state === 'failed') {
              notify('bad', `下载失败：${status.error}`);
            } else {
              notify('warn', '下载已取消');
            }
          }
        } catch (exc) {
          stopPolling();
          notify('bad', String(exc));
        }
      }, 800);
    } catch (exc) {
      notify('bad', `无法开始下载：${exc}`);
    }
  };

  const format = inspect?.format || {};
  const shape = format.shape || { n_cells: 0, n_genes: 0 };
  const methods = inspect?.methods || [];

  return (
    <div>
      <div className="cf-h1">数据</div>
      <div className="cf-sub">导入并检查数据是否满足各模型的前提。</div>

      <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 12 }}>
        <div>
          <div className="cf-sec">导入</div>
          <FormGroup label="来源">
            <HTMLSelect fill value={source} onChange={(event) => setSource(event.currentTarget.value)}>
              <option value="current">使用已有数据集</option>
              <option value="upload">上传文件</option>
              <option value="public">公开数据</option>
            </HTMLSelect>
          </FormGroup>

          {source === 'current' ? (
            <FormGroup label="数据集">
              <HTMLSelect fill value={dataset?.id || ''} onChange={(event) => onSelect(event.currentTarget.value)}>
                {meta.datasets.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
              </HTMLSelect>
            </FormGroup>
          ) : null}

          {source === 'upload' ? (
            <>
              <input ref={fileRef} type="file" accept=".h5ad,.loom,.h5,.csv,.tsv,.txt,.mtx,.zip,.gz,.tgz"
                     style={{ display: 'none' }}
                     onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file); }} />
              <Button fill icon="upload" onClick={() => fileRef.current?.click()}>选择文件…</Button>
              <div className="cf-sub" style={{ marginTop: 4 }}>支持 .h5ad / .loom / 10x / CSV / zip</div>
            </>
          ) : null}

          {source === 'public' ? (
            <>
              <FormGroup label="数据源">
                <HTMLSelect value={publicSource} fill
                            onChange={(event) => setPublicSource(event.currentTarget.value as 'geo' | 'cellxgene')}>
                  <option value="geo">NCBI GEO（GSE 编号）</option>
                  <option value="cellxgene">CELLxGENE Discover（数据集 UUID）</option>
                </HTMLSelect>
              </FormGroup>
              <FormGroup label={publicSource === 'geo' ? 'GEO 编号' : 'CELLxGENE 数据集 UUID'}
                         helperText={publicSource === 'geo'
                           ? '例：GSE84133。只支持该研究提供了可解析的补充文件。'
                           : '在 cellxgene.cziscience.com 数据集页复制 UUID。文件可能很大，下载不可取消。'}>
                <InputGroup value={publicId} onChange={(event) => setPublicId(event.currentTarget.value)}
                            placeholder={publicSource === 'geo' ? 'GSE84133' : 'xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'} />
              </FormGroup>
              <Button fill onClick={usePublic}
                      disabled={['queued', 'running', 'cancelling'].includes(download?.state || '')}>
                下载
              </Button>
              {download ? (
                <div style={{ marginTop: 8 }}>
                  <ProgressBar
                    value={download.total ? download.done / download.total : undefined}
                    intent={download.state === 'failed' ? 'danger' : download.state === 'done' ? 'success' : 'primary'}
                    stripes={download.state === 'running'} />
                  <div className="cf-sub" style={{ marginTop: 4 }}>
                    {download.message || download.state}
                    {download.total ? ` · ${(download.done / 1024 / 1024).toFixed(1)} / ${(download.total / 1024 / 1024).toFixed(1)} MB` : ''}
                  </div>
                  {['queued', 'running', 'cancelling'].includes(download.state) ? (
                    <Button small minimal intent="danger" style={{ marginTop: 4 }}
                            onClick={async () => {
                              try {
                                await api.cancelDownload(download.id);
                                notify('warn', '已请求取消下载');
                              } catch (exc) {
                                notify('bad', `取消失败：${exc}`);
                              }
                            }}>取消下载</Button>
                  ) : null}
                </div>
              ) : null}
            </>
          ) : null}

          <div className="cf-sec">数据声明</div>
          <Callout compact style={{ fontSize: 11.8 }}>
            核实来源后勾选；数值“像整数”不代表就是原始计数。
          </Callout>
          <Switch checked={rawX} label="X 是未归一化原始计数"
                  onChange={(event) => setRawX(event.currentTarget.checked)} />
          <Switch checked={rawSU} label="spliced/unspliced 是原始剪接计数"
                  onChange={(event) => setRawSU(event.currentTarget.checked)} />
        </div>

        <div>
          <div className="cf-sec">检查结果 <span className="h">{dataset?.label || '未选择'}</span></div>
          {error ? <Callout intent="danger" compact>{error}</Callout> : null}
          {loading ? <Spinner size={18} /> : null}

          {inspect ? (
            <>
              <HTMLTable compact bordered style={{ marginBottom: 10 }}>
                <tbody>
                  <tr><th style={{ width: 140 }}>细胞数</th><td>{shape.n_cells?.toLocaleString()}</td></tr>
                  <tr><th>基因数</th><td>{shape.n_genes?.toLocaleString()}</td></tr>
                  <tr><th>X 类型</th><td>{inspect.facts.x_kind_text || inspect.facts.x_kind}</td></tr>
                  <tr><th>剪接层</th><td>{(format.layers_present || []).join('、') || '无'}</td></tr>
                  <tr><th>剪接覆盖</th><td>{format.splice ? `${format.splice.both_positive_pct?.toFixed(1)}%` : '不适用'}</td></tr>
                  <tr><th>线粒体基因</th><td>{format.var?.mt_matched ?? '—'}</td></tr>
                  <tr><th>obs 列</th><td>{(format.obs || []).join('、') || '无'}</td></tr>
                </tbody>
              </HTMLTable>

              {(inspect.facts.errors || []).map((message) => (
                <Callout key={message} intent="danger" compact style={{ marginBottom: 4 }}>{message}</Callout>
              ))}
              {(inspect.facts.warnings || []).map((message) => (
                <Callout key={message} intent="warning" compact style={{ marginBottom: 4 }}>{message}</Callout>
              ))}

              <div className="cf-sec">方法适用性 <span className="h">按当前数据与声明计算</span></div>
              <HTMLTable compact striped>
                <thead><tr><th>方法</th><th>类别</th><th>状态</th><th>缺失</th></tr></thead>
                <tbody>
                  {methods.map((row) => (
                    <tr key={row.key}>
                      <td>{row.name}</td>
                      <td>{row.family}</td>
                      <td>
                        <Tag minimal intent={row.can_run ? 'success' : 'warning'}>
                          {row.can_run ? '可运行' : '受限'}
                        </Tag>
                      </td>
                      <td style={{ fontSize: 11.8 }}>{(row.blockers || []).join('；') || (row.missing || []).join('；') || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </HTMLTable>
            </>
          ) : (!loading && !error ? <Callout compact>尚未选择数据集。</Callout> : null)}
        </div>
      </div>
    </div>
  );
}
