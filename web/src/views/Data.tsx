import { useEffect, useRef, useState } from 'react';
import {
  Button, Callout, Checkbox, Dialog, FormGroup, HTMLSelect, HTMLTable, InputGroup, ProgressBar, Spinner, Switch, Tag,
} from '@blueprintjs/core';
import { api, Dataset, Inspect, Meta } from '../services/api';
import { createTracker, isAbortError } from '../services/async';
import {
  Declaration, datasetVersionKey, forgetDownload, lastDownload, rememberDownload,
} from '../services/context';

type DownloadStatus = {
  id: string; state: string; done: number; total: number;
  message: string; path: string; error: string;
};

const TERMINAL_STATES = ['done', 'failed', 'cancelled'];
const ACTIVE_STATES = ['queued', 'running', 'cancelling'];

export function DataView(props: {
  meta: Meta; dataset?: Dataset; onSelect: (id: string) => void;
  notify: (kind: 'ok' | 'warn' | 'bad', text: string) => void; refresh: () => void;
  declaration: Declaration; onDeclarationChange: (patch: Partial<Declaration>) => void;
}) {
  const { meta, dataset, onSelect, notify, refresh, declaration, onDeclarationChange } = props;
  const version = datasetVersionKey(dataset);
  const [inspect, setInspect] = useState<Inspect | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [source, setSource] = useState('current');
  const [localPath, setLocalPath] = useState('');
  const [publicId, setPublicId] = useState('');
  const [publicSource, setPublicSource] = useState<'geo' | 'cellxgene'>('geo');
  const [download, setDownload] = useState<DownloadStatus | null>(null);
  const [preview, setPreview] = useState<null | { id: string; data: Record<string, any> }>(null);
  const [orientation, setOrientation] = useState('cells_by_genes');
  const [metaCols, setMetaCols] = useState<string[]>([]);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const inspectTracker = useRef(createTracker()).current;

  // 数据检查：切换数据集/声明时取消旧请求；旧响应即使晚到也不能覆盖新数据（F31）。
  useEffect(() => {
    const controller = new AbortController();
    const token = inspectTracker.begin();
    if (!dataset) {
      setInspect(null);
      setError('');
      setLoading(false);
      return () => controller.abort();
    }
    // 同一数据集重新检查（如切换声明）时保留旧结果避免闪烁；切换数据集时立即清空。
    setInspect((previous) => (previous && previous.path === dataset.path ? previous : null));
    setLoading(true);
    setError('');
    api.inspect(dataset.path, declaration.rawX, declaration.rawSU, controller.signal)
      .then((data) => { if (inspectTracker.current(token)) setInspect(data); })
      .catch((exc) => { if (inspectTracker.current(token) && !isAbortError(exc)) setError(String(exc)); })
      .finally(() => { if (inspectTracker.current(token)) setLoading(false); });
    return () => controller.abort();
  }, [version, dataset?.path, declaration.rawX, declaration.rawSU]);

  // 下载状态：离开本页后重新进入仍能恢复（F31）。
  const pollRef = useRef<number | null>(null);
  const downloadIdRef = useRef('');

  const stopPolling = () => {
    if (pollRef.current) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const pollDownload = (id: string) => {
    stopPolling();
    downloadIdRef.current = id;
    pollRef.current = window.setInterval(async () => {
      try {
        const status = await api.downloadStatus(id);
        if (downloadIdRef.current !== id) return; // 已经不是当前下载
        setDownload(status);
        if (TERMINAL_STATES.includes(status.state)) {
          stopPolling();
          if (status.state === 'done') {
            refresh();
            notify('ok', `下载完成：${status.path}`);
          } else if (status.state === 'failed') {
            forgetDownload();
            notify('bad', `下载失败：${status.error}`);
          } else {
            forgetDownload();
            notify('warn', '下载已取消');
          }
        }
      } catch (exc) {
        if (downloadIdRef.current !== id) return;
        stopPolling();
        forgetDownload();
        setDownload(null);
        notify('bad', `下载状态读取失败：${exc}`);
      }
    }, 800);
  };

  useEffect(() => {
    let cancelled = false;
    const saved = lastDownload();
    if (!saved) return undefined;
    setDownload({
      id: saved.id, state: 'queued', done: 0, total: 0,
      message: '正在恢复上次下载状态…', path: '', error: '',
    });
    api.downloadStatus(saved.id)
      .then((status) => {
        if (cancelled) return;
        setDownload(status);
        if (!TERMINAL_STATES.includes(status.state)) pollDownload(status.id);
        else if (status.state === 'done') refresh(); // 让新下载的数据集出现在列表里
        else if (status.state === 'failed' || status.state === 'cancelled') forgetDownload();
      })
      .catch(() => {
        if (cancelled) return;
        forgetDownload();
        setDownload(null);
      });
    return () => {
      cancelled = true;
      stopPolling();
      downloadIdRef.current = '';
    };
    // 仅在进入数据页时恢复一次
  }, []);

  const upload = async (file: File) => {
    try {
      if (/\.(csv|tsv|txt)$/i.test(file.name)) {
        const body = await api.tablePreview(file);
        setPreview({ id: body.preview_id, data: body.preview });
        setOrientation(body.preview.orientation_guess || 'cells_by_genes');
        setMetaCols(body.preview.metadata_candidates || []);
        notify('warn', '表格已预览：请确认方向与元数据列后再导入（不会自动猜）');
        return;
      }
      const result = await api.upload(file);
      notify('ok', `已导入：${result.path}`);
      refresh();
      onSelect(result.id);
    } catch (exc) {
      notify('bad', `导入失败：${exc}`);
    }
  };

  const confirmImport = async () => {
    if (!preview) return;
    setImporting(true);
    try {
      const result = await api.tableImport({
        preview_id: preview.id, orientation, metadata_columns: metaCols,
      });
      notify('ok', `已导入：${result.name}（${result.n_cells} 细胞 × ${result.n_genes} 基因）`);
      setPreview(null);
      refresh();
      onSelect(result.id);
    } catch (exc) {
      notify('bad', `确认导入失败：${exc}`);
    } finally {
      setImporting(false);
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

  const usePublic = async () => {
    stopPolling();
    downloadIdRef.current = '';
    try {
      const started = await api.startDownload(publicSource, publicId);
      setDownload({
        id: started.id, state: 'queued', done: 0, total: 0,
        message: '排队中', path: '', error: '',
      });
      rememberDownload({
        id: started.id, startedAt: Date.now(), source: publicSource, identifier: publicId,
      });
      notify('ok', '下载已在后台开始，可以继续用其他页面；重新进入本页会恢复显示进度');
      pollDownload(started.id);
    } catch (exc) {
      notify('bad', `无法开始下载：${exc}`);
    }
  };

  // 渲染时再做一次数据版本核对：切换数据集的同一帧也不会显示上一份数据的检查结果。
  const currentInspect = inspect && dataset && inspect.path === dataset.path ? inspect : null;
  const format = currentInspect?.format || {};
  const shape = format.shape || { n_cells: 0, n_genes: 0 };
  const methods = currentInspect?.methods || [];

  return (
    <div>
      <div className="cf-h1">数据</div>
      <div className="cf-sub">导入并检查数据是否满足各模型的前提。</div>

      <div className="cf-data-grid">
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
                      disabled={ACTIVE_STATES.includes(download?.state || '')}>
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
                  {ACTIVE_STATES.includes(download.state) ? (
                    <Button small minimal intent="danger" style={{ marginTop: 4 }}
                            onClick={async () => {
                              try {
                                await api.cancelDownload(download.id);
                                notify('warn', '已请求取消下载');
                              } catch (exc) {
                                notify('bad', `取消失败：${exc}`);
                              }
                            }}>取消下载</Button>
                  ) : (
                    <Button small minimal style={{ marginTop: 4 }}
                            onClick={() => { forgetDownload(); setDownload(null); }}>
                      清除记录
                    </Button>
                  )}
                </div>
              ) : null}
            </>
          ) : null}

          <div className="cf-sec">数据声明 <span className="h">按数据内容版本保存</span></div>
          <Callout compact style={{ fontSize: 11.8 }}>
            核实来源后勾选；数值“像整数”不代表就是原始计数。
            声明只对当前数据版本有效；切换数据集或文件被替换后必须重新确认。
          </Callout>
          <div className="cf-sub" style={{ margin: '4px 0 6px' }}>
            版本：<span className="cf-mono">{version || '未选择数据'}</span>
            {declaration.confirmedAt
              ? ` · 上次确认 ${new Date(declaration.confirmedAt).toLocaleString('zh-CN')}`
              : ' · 尚未确认'}
          </div>
          <Switch checked={declaration.rawX} disabled={!dataset}
                  label="X 是未归一化原始计数"
                  onChange={(event) => onDeclarationChange({ rawX: event.currentTarget.checked })} />
          <Switch checked={declaration.rawSU} disabled={!dataset}
                  label="spliced/unspliced 是原始剪接计数"
                  onChange={(event) => onDeclarationChange({ rawSU: event.currentTarget.checked })} />
        </div>

        <div>
          <div className="cf-sec">检查结果 <span className="h">{dataset?.label || '未选择'}</span></div>
          {error ? <Callout intent="danger" compact>{error}</Callout> : null}
          {loading ? <Spinner size={18} /> : null}

          {currentInspect ? (
            <>
              <HTMLTable compact bordered style={{ marginBottom: 10 }}>
                <tbody>
                  <tr><th style={{ width: 140 }}>细胞数</th><td>{shape.n_cells?.toLocaleString()}</td></tr>
                  <tr><th>基因数</th><td>{shape.n_genes?.toLocaleString()}</td></tr>
                  <tr><th>X 类型</th><td>{currentInspect.facts.x_kind_text || currentInspect.facts.x_kind}</td></tr>
                  <tr><th>剪接层</th><td>{(format.layers_present || []).join('、') || '无'}</td></tr>
                  <tr><th>剪接覆盖</th><td>{format.splice ? `${format.splice.both_positive_pct?.toFixed(1)}%` : '不适用'}</td></tr>
                  <tr><th>线粒体基因</th><td>{format.var?.mt_matched ?? '—'}</td></tr>
                  <tr><th>obs 列</th><td>{(format.obs || []).join('、') || '无'}</td></tr>
                </tbody>
              </HTMLTable>

              {(currentInspect.facts.errors || []).map((message) => (
                <Callout key={message} intent="danger" compact style={{ marginBottom: 4 }}>{message}</Callout>
              ))}
              {(currentInspect.facts.warnings || []).map((message) => (
                <Callout key={message} intent="warning" compact style={{ marginBottom: 4 }}>{message}</Callout>
              ))}

              <div className="cf-sec">方法适用性 <span className="h">按当前数据与声明计算</span></div>
              <div className="cf-sub" style={{ marginBottom: 4 }}>
                本表只反映数据与声明；起点细胞、分组列与先验 GRN 在「模型分析」填写后再判定。
              </div>
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

      <Dialog isOpen={!!preview} onClose={() => setPreview(null)} title="确认表格导入"
              style={{ width: 'min(560px, 92vw)' }}>
        <div className="cf-dialog-body" style={{ padding: 14 }}>
          {preview ? (
            <>
              <Callout compact intent="warning" style={{ marginBottom: 10 }}>
                方向与元数据列不会自动替换你的选择；缺失/非数值表达会被拒绝。
                预览：{preview.data.rows_sampled} 行 × {preview.data.columns} 列，
                样本缺失 {preview.data.missing_in_sample} 个，表头重复 {preview.data.header_duplicates} 个。
                推断理由：{preview.data.orientation_reason}
              </Callout>
              <FormGroup label="表格方向">
                <HTMLSelect fill value={orientation} onChange={(event) => setOrientation(event.currentTarget.value)}>
                  <option value="cells_by_genes">细胞 × 基因（行是细胞，列是基因）</option>
                  <option value="genes_by_cells">基因 × 细胞（行是基因，列是细胞，会转置）</option>
                </HTMLSelect>
              </FormGroup>
              <FormGroup label="元数据列（不参与表达矩阵）">
                {(preview.data.metadata_candidates || []).length
                  ? (preview.data.metadata_candidates || []).map((column: string) => (
                    <Checkbox key={column} checked={metaCols.includes(column)}
                              label={column}
                              onChange={(event) => {
                                const checked = event.currentTarget.checked;
                                setMetaCols((current) => checked
                                  ? [...current, column]
                                  : current.filter((item) => item !== column));
                              }} />
                  ))
                  : <div className="cf-sub">未发现文档列出的元数据列；如需注释，请放入 h5ad 的 obs。</div>}
              </FormGroup>
              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 6 }}>
                <Button onClick={() => setPreview(null)}>取消</Button>
                <Button intent="primary" loading={importing} onClick={() => void confirmImport()}>
                  确认导入
                </Button>
              </div>
            </>
          ) : null}
        </div>
      </Dialog>
    </div>
  );
}
