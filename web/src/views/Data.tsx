import { useEffect, useRef, useState } from 'react';
import {
  Button, Callout, FormGroup, HTMLSelect, HTMLTable, InputGroup, ProgressBar, Spinner, Switch, Tag,
} from '@blueprintjs/core';
import { api, Dataset, GeoFile, GeoManifest, Inspect, Meta, StorageInfo } from '../services/api';
import { createTracker, isAbortError } from '../services/async';
import { classifyFailure, isTransientFailure } from '../services/net';
import {
  createDownloadMonitor, DownloadMonitor, lostMessage, transientFailureMessage,
} from '../services/download-monitor';
import {
  Declaration, datasetVersionKey, forgetDownload, lastDownload, rememberDownload,
} from '../services/context';

type DownloadStatus = {
  id: string; state: string; done: number; total: number;
  message: string; path: string; error: string;
};

const TERMINAL_STATES = ['done', 'failed', 'cancelled'];

const FILE_KIND_LABEL: Record<string, string> = {
  h5ad: 'h5ad', loom: 'loom', '10x_h5': '10x h5', '10x_mtx': '10x mtx',
  archive: '压缩包', table: '表达表', other: '其他',
};

// 剪接证据等级：只有真正解析过文件内部才能说「已验证」。
const SPLICE_LABEL: Record<string, string> = {
  verified_by_content: '已验证含剪接层',
  metadata_supported: '元数据提示含剪接',
  filename_only: '仅文件名暗示',
  absent: '未提示',
};

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return '未知';
  if (value < 1024) return value + ' B';
  if (value < 1024 ** 2) return (value / 1024).toFixed(1) + ' KB';
  if (value < 1024 ** 3) return (value / 1024 ** 2).toFixed(1) + ' MB';
  return (value / 1024 ** 3).toFixed(2) + ' GB';
}
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
  // 轮询与本地服务失联中：任务仍然保留，只是显示的不是最新进度。
  const [stalled, setStalled] = useState(false);
  // P3-01：GEO 文件清单（查询阶段绝不下载任何文件）
  const [manifest, setManifest] = useState<GeoManifest | null>(null);
  const [manifestError, setManifestError] = useState('');
  const [manifestLoading, setManifestLoading] = useState(false);
  const [selectedFile, setSelectedFile] = useState('');
  // P3-02：数据与存储
  const [storage, setStorage] = useState<StorageInfo | null>(null);
  const [rootDraft, setRootDraft] = useState('');
  const [rootConfirm, setRootConfirm] = useState(false);
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
  //
  // 修复的缺陷：旧实现用 setInterval(800ms)，任何一次异常都会
  //   stopPolling() + forgetDownload() + setDownload(null)，
  // 于是一次本机服务抖动就让一个仍在后台下载的任务从界面上消失，
  // 用户看到的是失败，实际上文件还在下载。
  // 现在由 download-monitor 状态机区分：暂时失联（保留任务并重试）、
  // 任务确实不存在（才清除记录）、业务错误（停止并提示）。
  const downloadIdRef = useRef('');
  const downloadHandlers = useRef({ notify, refresh });
  downloadHandlers.current = { notify, refresh };
  const monitorRef = useRef<DownloadMonitor<DownloadStatus> | null>(null);
  if (monitorRef.current === null) {
    monitorRef.current = createDownloadMonitor<DownloadStatus>({
      fetchStatus: (id) => api.downloadStatus(id),
      isTerminal: (status) => TERMINAL_STATES.includes(status.state),
      classifyFailure,
      intervalMs: 800,
      onEvent: (event) => {
        const handlers = downloadHandlers.current;
        if (event.type === 'status') {
          setDownload(event.status);
          setStalled(false);
          if (!TERMINAL_STATES.includes(event.status.state)) return;
          if (event.status.state === 'done') {
            handlers.refresh();
            handlers.notify('ok', `下载完成：${event.status.path}`);
          } else if (event.status.state === 'failed') {
            forgetDownload();
            handlers.notify('bad', `下载失败：${event.status.error}`);
          } else {
            forgetDownload();
            handlers.notify('warn', '下载已取消');
          }
          return;
        }
        if (event.type === 'retry') {
          setStalled(true);
          setDownload((previous) => (previous && previous.id === downloadIdRef.current
            ? { ...previous, message: transientFailureMessage(event.failures) }
            : previous));
          return;
        }
        if (event.type === 'gone') {
          // 只有“任务确实不存在”才清除记录（服务重启后状态会被标记为中断）。
          downloadIdRef.current = '';
          forgetDownload();
          setStalled(false);
          setDownload(null);
          handlers.notify('bad',
            '下载任务状态已不存在（本机服务可能重启过）。若文件已下载完成，请刷新数据集列表。');
          return;
        }
        setStalled(true);
        if (event.type === 'lost') handlers.notify('warn', lostMessage(event.failures));
        else handlers.notify('bad', `下载状态读取失败：${event.error}`);
      },
    });
  }

  const stopPolling = () => {
    monitorRef.current?.stop();
    downloadIdRef.current = '';
  };

  const pollDownload = (id: string) => {
    downloadIdRef.current = id;
    monitorRef.current?.start(id);
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
      .catch((exc) => {
        if (cancelled) return;
        // 只有任务确实不存在才丢弃记录；暂时失联要保留，用户重进本页还能恢复。
        if (classifyFailure(exc) === 'missing') {
          forgetDownload();
          setDownload(null);
          notify('warn', '上次的下载任务已不存在（本机服务可能重启过）。');
        } else {
          setStalled(true);
          notify('warn', `暂时读不到上次下载的状态：${exc}`);
        }
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

  // 对账：POST 在传输层失败时，用幂等的 GET 判断任务是否其实已经创建。
  // 绝不重发 POST —— 那可能让同一份数据被创建成两个任务、下两遍。
  const reconcileDownload = async (
    source: string, identifier: string,
  ): Promise<DownloadStatus | null> => {
    try {
      const listing = await api.downloads();
      const fresh = listing.downloads.filter((task) => task.source === source
        && task.identifier === identifier
        && Date.now() / 1000 - task.created < 120);
      if (!fresh.length) return null;
      const newest = fresh.reduce((a, b) => (a.created >= b.created ? a : b));
      return {
        id: newest.id, state: newest.state, done: newest.done, total: newest.total,
        message: newest.message, path: newest.path, error: newest.error,
      };
    } catch {
      return null;
    }
  };

  const usePublic = async () => {
    stopPolling();
    setStalled(false);
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
      if (isTransientFailure(exc)) {
        const adopted = await reconcileDownload(publicSource, publicId);
        if (adopted) {
          setDownload(adopted);
          rememberDownload({
            id: adopted.id, startedAt: Date.now(), source: publicSource, identifier: publicId,
          });
          notify('warn', '请求被中断，但下载任务已在后台创建；继续显示它的进度。');
          pollDownload(adopted.id);
          return;
        }
      }
      notify('bad', `无法开始下载：${exc}`);
    }
  };

  // ---- P3-01：先查清单，再只下载用户选中的那一个文件 ----
  const loadManifest = async () => {
    const gse = publicId.trim().toUpperCase();
    if (!gse) {
      notify('warn', '请先填写 GSE 编号');
      return;
    }
    setManifestLoading(true);
    setManifestError('');
    setManifest(null);
    setSelectedFile('');
    try {
      const data = await api.geoManifest(gse);
      setManifest(data);
      const importable = data.files.filter((file) => file.importable);
      // 只有一个可导入文件时预选它（仍需要用户点“下载选中文件”）
      if (importable.length === 1) setSelectedFile(importable[0].url);
      if (!data.file_count) setManifestError('该 Series 没有可导入的数据文件（可能只有 SRA 原始数据）。');
    } catch (exc) {
      setManifestError(String(exc));
    } finally {
      setManifestLoading(false);
    }
  };

  const downloadSelected = async () => {
    if (!manifest || !selectedFile) {
      notify('warn', '请先查询文件清单并选择一个文件');
      return;
    }
    stopPolling();
    setStalled(false);
    const gse = manifest.accession;
    try {
      const started = await api.geoDownload(gse, [selectedFile]);
      const id = started.ids[0];
      setDownload({
        id, state: 'queued', done: 0, total: 0,
        message:
          '已提交：' + started.files[0], path: '', error: '',
      });
      rememberDownload({ id, startedAt: Date.now(), source: 'geo', identifier: gse });
      notify('ok', '已开始下载 1 个文件：' + started.files[0]);
      pollDownload(id);
    } catch (exc) {
      if (isTransientFailure(exc)) {
        const adopted = await reconcileDownload('geo', gse);
        if (adopted) {
          setDownload(adopted);
          rememberDownload({ id: adopted.id, startedAt: Date.now(), source: 'geo', identifier: gse });
          notify('warn', '请求被中断，但下载任务已创建；继续显示进度。');
          pollDownload(adopted.id);
          return;
        }
      }
      notify('bad', '无法开始下载：' + String(exc));
    }
  };

  // ---- P3-02：数据根目录（只影响以后新文件的落点，不迁移已有数据）----
  useEffect(() => {
    api.storage().then(setStorage).catch(() => setStorage(null));
  }, []);

  const applyDataRoot = async () => {
    const target = rootDraft.trim();
    if (!target) {
      notify('warn', '请填写数据目录，例如 D:\\CytelleData');
      return;
    }
    if (!rootConfirm) {
      notify('warn', '切换数据目录需要先勾选确认；已有数据不会被移动。');
      return;
    }
    try {
      const result = await api.setDataRoot(target, true);
      notify('ok', '数据目录已切换：' + result.data_root);
      setStorage(await api.storage());
      setRootDraft('');
      setRootConfirm(false);
    } catch (exc) {
      notify('bad', '切换失败：' + String(exc));
    }
  };

  const cleanStaging = async (dryRun: boolean) => {
    try {
      const result = await api.cleanupStorage(24, dryRun);
      notify('ok', (dryRun ? '可清理 ' : '已清理 ') +
        (result.removed.length) + ' 个临时任务目录，约 ' +
        (result.removed_bytes / 1024 / 1024).toFixed(1) + ' MB');
      setStorage(await api.storage());
    } catch (exc) {
      notify('bad', '清理失败：' + String(exc));
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
              {publicSource === 'geo' ? (
                <>
                  <Button fill icon="search" loading={manifestLoading}
                          disabled={!publicId.trim()}
                          onClick={() => { void loadManifest(); }}>
                    查询文件清单（不下载）
                  </Button>
                  {manifestError ? (
                    <Callout compact intent="danger" style={{ marginTop: 6 }}>{manifestError}</Callout>
                  ) : null}
                  {manifest ? (
                    <div style={{ marginTop: 8 }}>
                      <div className="cf-sub" style={{ marginBottom: 4 }}>
                        <b>{manifest.info.title || manifest.accession}</b>
                        {' · '}{manifest.accession}
                        {manifest.info.taxon ? ' · ' + manifest.info.taxon : ''}
                        {manifest.info.n_samples ? ' · ' + manifest.info.n_samples + ' 个样本' : ''}
                        {' · '}{manifest.file_count} 个可尝试导入的文件
                      </div>
                      <div className="cf-sub" style={{ marginBottom: 6 }}>
                        只下载勾选的文件；不会自动下载整个 Series。
                        大小来自 NCBI 目录页（四舍五入）。
                      </div>
                      <HTMLTable compact bordered style={{ marginBottom: 6 }}>
                        <thead>
                          <tr><th style={{ width: 28 }}></th><th>文件</th><th style={{ width: 78 }}>大小</th>
                            <th style={{ width: 70 }}>类型</th><th style={{ width: 110 }}>剪接证据</th></tr>
                        </thead>
                        <tbody>
                          {manifest.files.map((file: GeoFile) => (
                            <tr key={file.url} style={{ opacity: file.importable ? 1 : 0.5 }}>
                              <td>
                                <input type="radio" name="geo-file"
                                       disabled={!file.importable}
                                       checked={selectedFile === file.url}
                                       onChange={() => setSelectedFile(file.url)} />
                              </td>
                              <td title={file.name}>{file.name}</td>
                              <td>{file.size_text || '未知'}</td>
                              <td>{FILE_KIND_LABEL[file.kind] || file.kind}</td>
                              <td>{SPLICE_LABEL[file.splice_evidence] || file.splice_evidence}</td>
                            </tr>
                          ))}
                        </tbody>
                      </HTMLTable>
                      <div className="cf-sub" style={{ marginBottom: 6 }}>
                        已选 {selectedFile ? '1' : '0'} 个 ·{' '}
                        {formatBytes(manifest.files.find((f) => f.url === selectedFile)?.size_bytes)}
                        {storage ? ' · 数据目录可用 ' + formatBytes(storage.free_bytes) : ''}
                      </div>
                      <Button fill intent="primary" icon="download"
                              disabled={!selectedFile || ACTIVE_STATES.includes(download?.state || '')}
                              onClick={() => { void downloadSelected(); }}>
                        下载选中文件
                      </Button>
                    </div>
                  ) : null}
                </>
              ) : (
                <Button fill onClick={usePublic}
                        disabled={ACTIVE_STATES.includes(download?.state || '')}>
                  下载
                </Button>
              )}
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
                  {stalled ? (
                    <Callout compact intent="warning" style={{ marginTop: 6, fontSize: 12 }}>
                      与本地服务的连接中断：下载任务仍在后台运行，这里显示的是最后一次读到的状态。
                      <Button small minimal icon="refresh" style={{ marginLeft: 8 }}
                              onClick={() => { setStalled(false); pollDownload(download.id); }}>
                        重新读取状态
                      </Button>
                    </Callout>
                  ) : null}
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

          <details style={{ marginTop: 14 }}>
            <summary className="cf-sec" style={{ cursor: 'pointer' }}>
              数据与存储 <span className="h">
                {storage ? storage.data_root : '读取中…'}
              </span>
            </summary>
            {storage ? (
              <div style={{ marginTop: 6 }}>
                <div className="cf-sub" style={{ marginBottom: 4 }}>
                  可用 {formatBytes(storage.free_bytes)} / 共 {formatBytes(storage.total_bytes)}
                  {' · '}临时文件占用 {formatBytes(storage.staging.total_bytes)}
                  {'（'}{storage.staging.count} 个任务目录）
                  {storage.is_default ? ' · 使用项目内默认目录' : ''}
                </div>
                <div className="cf-sub" style={{ marginBottom: 4 }}>
                  大文件（下载、解压、格式转换）都写到这个目录，不会占用系统盘临时目录。
                </div>
                <InputGroup value={rootDraft} fill
                            placeholder="例如 D:\CytelleData"
                            onChange={(event) => setRootDraft(event.currentTarget.value)} />
                <label className="cf-sub" style={{ display: 'block', margin: '4px 0' }}>
                  <input type="checkbox" checked={rootConfirm}
                         onChange={(event) => setRootConfirm(event.currentTarget.checked)} />
                  {' '}我确认切换数据目录（只影响以后新文件；已有数据不会被移动）
                </label>
                <Button small onClick={() => { void applyDataRoot(); }}>切换数据目录</Button>
                {' '}
                <Button small minimal onClick={() => { void cleanStaging(true); }}>检查可清理临时文件</Button>
                {' '}
                <Button small minimal onClick={() => { void cleanStaging(false); }}>清理超过 24 小时的临时文件</Button>
              </div>
            ) : null}
          </details>

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
    </div>
  );
}
