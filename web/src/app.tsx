import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alignment, Button, Callout, Dialog, Drawer, H3, HTMLSelect, InputGroup,
  Menu, MenuDivider, MenuItem, Navbar, NavbarDivider, NavbarGroup, NavbarHeading,
  Spinner, Tag,
} from '@blueprintjs/core';
import { api, Dataset, Meta } from './services/api';
import { Overview } from './views/Overview';
import { DataView } from './views/Data';
import { ModelView } from './views/Model';
import { ResultsView } from './views/Results';
import { ReportsView } from './views/Reports';

export type ViewKey = 'overview' | 'data' | 'model' | 'results' | 'reports';

const HELP: Record<ViewKey, { title: string; body: string }> = {
  overview: {
    title: '概览',
    body: '查看项目内的数据集、任务与流程位置。任务在后台独立运行，切换视图不会中断；结果保存在 results/ 下。',
  },
  data: {
    title: '数据',
    body: '导入并检查数据。支持 .h5ad / .loom / 10x / CSV / TSV / zip，矩阵方向自动判断。原始计数与剪接层需要核实来源后勾选声明；普通表达矩阵不能做 RNA velocity。',
  },
  model: {
    title: '模型分析',
    body: '选择方法、确认前置条件、配置参数并提交。每个方法一个独立任务；“可运行”表示有适配器且本机依赖齐全。前置条件不足时按钮置灰并写明缺失项。',
  },
  results: {
    title: '分析结果',
    body: '查看实际产出：图、指标、基因表与运行记录。箭头与拟时序是模型推断，不是实验验证；不同模型的指标不可直接排名。',
  },
  reports: {
    title: '报告',
    body: 'A 生物学分析报告记录可计算的结果与解释边界；B 技术与复现报告记录方法、版本、参数、环境与日志。报告含本机路径与样本 ID，外发前请审阅。',
  },
};

const STEPS: { key: ViewKey; label: string }[] = [
  { key: 'overview', label: '概览' },
  { key: 'data', label: '数据' },
  { key: 'model', label: '模型分析' },
  { key: 'results', label: '分析结果' },
  { key: 'reports', label: '报告' },
];

export function App() {
  const [view, setView] = useState<ViewKey>(() => (localStorage.getItem('cf.view') as ViewKey) || 'overview');
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<string>('');
  const [tick, setTick] = useState(0);
  const [datasetId, setDatasetId] = useState<string>(() => localStorage.getItem('cf.dataset') || '');
  const [helpOpen, setHelpOpen] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'warn' | 'bad'; text: string } | null>(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [projectName, setProjectName] = useState('');
  const [envOpen, setEnvOpen] = useState(false);
  const [envData, setEnvData] = useState<any>(null);
  const [envError, setEnvError] = useState('');
  const [createOpen, setCreateOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState('');
  const [manageOpen, setManageOpen] = useState(false);
  const [health, setHealth] = useState<any>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.meta();
      // 正式界面不展示内置模拟示例（测试数据只保留在测试与 API 层）
      const visible = { ...data, datasets: data.datasets.filter((item) => item.source !== '示例') };
      setMeta(visible);
      setError('');
      if (!datasetId && data.datasets.length) {
        setDatasetId(data.datasets[0].id);
      }
    } catch (exc) {
      setError(String(exc));
    }
  }, [datasetId]);

  useEffect(() => { void load(); }, [load, tick]);

  useEffect(() => { localStorage.setItem('cf.view', view); }, [view]);

  // 有任务在跑时，每 10 秒刷新一次真实状态（不伪造进度）
  useEffect(() => {
    if (!meta) return undefined;
    const active = (meta.counts?.running || 0) + (meta.counts?.queued || 0);
    if (!active) return undefined;
    const timer = window.setInterval(() => setTick((value) => value + 1), 10000);
    return () => window.clearInterval(timer);
  }, [meta?.counts?.running, meta?.counts?.queued]);

  const dataset: Dataset | undefined = useMemo(
    () => meta?.datasets.find((item) => item.id === datasetId) || meta?.datasets[0],
    [meta, datasetId],
  );

  useEffect(() => {
    if (dataset) localStorage.setItem('cf.dataset', dataset.id);
  }, [dataset]);

  const notify = (kind: 'ok' | 'warn' | 'bad', text: string) => {
    setNotice({ kind, text });
    window.setTimeout(() => setNotice(null), 6000);
  };

  const refresh = () => setTick((value) => value + 1);

  useEffect(() => {
    let alive = true;
    const probe = async () => {
      try {
        const data = await api.health();
        if (alive) setHealth(data);
      } catch {
        if (alive) setHealth({ ok: false });
      }
    };
    probe();
    const timer = window.setInterval(probe, 10000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <Callout intent="danger" title="无法连接本地服务">
          {error}
          <div style={{ marginTop: 8 }}>
            <Button onClick={refresh}>重试</Button>
          </div>
        </Callout>
      </div>
    );
  }

  if (!meta) {
    return (
      <div style={{ display: 'flex', height: '100vh', alignItems: 'center', justifyContent: 'center' }}>
        <Spinner /> <span style={{ marginLeft: 8 }}>正在连接本地分析服务…</span>
      </div>
    );
  }

  const counts = meta.counts || {};
  const help = HELP[view];

  return (
    <>
      <Navbar>
        <NavbarGroup align={Alignment.LEFT}>
          <NavbarHeading>CellFlow</NavbarHeading>
          <NavbarDivider />
          <span className="cf-crumb">
            <b>{meta.project?.name || '未命名项目'}</b>
            {dataset ? <> / {dataset.label}</> : null}
          </span>
        </NavbarGroup>
        <NavbarGroup align={Alignment.RIGHT}>
          <Tag minimal>排队 {counts.queued || 0}</Tag>
          <Tag minimal>运行 {counts.running || 0}</Tag>
          <Tag minimal>完成 {counts.completed || 0}</Tag>
          {counts.failed ? <Tag intent="danger" minimal>失败 {counts.failed}</Tag> : null}
          <NavbarDivider />
          <Button icon="refresh" minimal onClick={refresh}>刷新</Button>
          <Button icon="help" minimal active={helpOpen} onClick={() => setHelpOpen(true)}>
            说明
          </Button>
          <HTMLSelect
            value={meta.active_project?.id || (meta.projects?.[0]?.id || '')}
            style={{ width: 150, height: 28, marginLeft: 6 }}
            onChange={async (event) => {
              try {
                await api.activateProject(event.currentTarget.value);
                refresh();
              } catch (exc) {
                notify('bad', `切换项目失败：${exc}`);
              }
            }}>
            {(meta.projects || []).filter((project) => !project.archived).map((project) => (
              <option key={project.id} value={project.id}>{project.name}</option>
            ))}
          </HTMLSelect>
          <Button icon="add" minimal onClick={() => { setNewProjectName(''); setCreateOpen(true); }}>新建</Button>
          <Button icon="properties" minimal onClick={() => setManageOpen(true)}>管理</Button>
          <Button icon="edit" minimal onClick={() => { setProjectName(meta.project?.name || ''); setRenameOpen(true); }}>
            项目
          </Button>
          <Button icon="desktop" minimal onClick={async () => {
            setEnvOpen(true);
            setEnvError('');
            try {
              setEnvData(await api.env());
            } catch (exc) {
              setEnvError(String(exc));
            }
          }}>
            环境
          </Button>
        </NavbarGroup>
      </Navbar>

      <div className="cf-shell">
        <div className="cf-side">
          <Menu>
            {STEPS.map((step) => (
              <MenuItem
                key={step.key}
                text={step.label}
                active={view === step.key}
                onClick={() => setView(step.key)}
              />
            ))}
            <MenuDivider />
            <div style={{ padding: '4px 8px' }}>
              <div className="cf-sub">当前数据</div>
              <HTMLSelect
                value={dataset?.id || ''}
                fill
                minimal
                onChange={(event) => { setDatasetId(event.currentTarget.value); }}
              >
                {meta.datasets.map((item) => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
                {meta.datasets.length === 0 ? <option value="">（无数据）</option> : null}
              </HTMLSelect>
            </div>
          </Menu>
        </div>

        <div className="cf-main">
          {dataset ? (
            <div className="cf-crumb" style={{ marginBottom: 6 }}>
              {dataset.source} · {dataset.kind} · {dataset.size}
            </div>
          ) : null}

          {notice ? (
            <div style={{ marginBottom: 8 }}>
              <Callout intent={notice.kind === 'ok' ? 'success' : notice.kind === 'warn' ? 'warning' : 'danger'} compact>
                {notice.text}
              </Callout>
            </div>
          ) : null}

          {view === 'overview' ? (
            <Overview meta={meta} dataset={dataset} refresh={refresh} notify={notify} onNavigate={setView} />
          ) : null}
          {view === 'data' ? (
            <DataView meta={meta} dataset={dataset} onSelect={(id) => { setDatasetId(id); refresh(); }} notify={notify} refresh={refresh} />
          ) : null}
          {view === 'model' ? (
            <ModelView meta={meta} dataset={dataset} refresh={refresh} notify={notify} />
          ) : null}
          {view === 'results' ? (
            <ResultsView meta={meta} dataset={dataset} notify={notify} tick={tick} />
          ) : null}
          {view === 'reports' ? (
            <ReportsView meta={meta} dataset={dataset} notify={notify} />
          ) : null}
        </div>
      </div>

      <div className="cf-statusbar">
        <span className={health?.ok ? 'cf-status-ok' : 'cf-status-bad'}>
          ● {health ? (health.ok ? '本地服务已连接' : '本地服务断开') : '连接中…'}
        </span>
        <span>队列：运行 {health?.queue?.running ?? 0} · 排队 {health?.queue?.queued ?? 0}</span>
        <span>设备：{health?.device || '—'}</span>
        <span>项目：{health?.projects || meta.active_project?.id || '—'}</span>
        <span style={{ marginLeft: 'auto' }}>任务不会因关闭页面而中断</span>
      </div>

      <Drawer isOpen={helpOpen} onClose={() => setHelpOpen(false)} size={330} title={help.title + ' · 说明'}>
        <div className="cf-help-body" style={{ padding: 14 }}>
          <p>{help.body}</p>
          <H3 style={{ fontSize: 12.8 }}>操作原则</H3>
          <ul>
            <li>参数旁的小问号说明含义与何时需要修改。</li>
            <li>前置条件不满足时，界面会写明缺什么，不会自动换方法。</li>
            <li>任务状态来自真实队列，不显示伪造的进度百分比。</li>
          </ul>
        </div>
      </Drawer>

      <Dialog isOpen={renameOpen} onClose={() => setRenameOpen(false)} title="项目名称" style={{ width: 360 }}>
        <div style={{ padding: 12 }}>
          <InputGroup value={projectName} onChange={(event) => setProjectName(event.currentTarget.value)} />
          <div style={{ marginTop: 10, display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
            <Button onClick={() => setRenameOpen(false)}>取消</Button>
            <Button intent="primary" onClick={async () => {
              try {
                await api.renameProject(projectName);
                setRenameOpen(false);
                refresh();
              } catch (exc) {
                notify('bad', String(exc));
              }
            }}>保存</Button>
          </div>
        </div>
      </Dialog>

      <Dialog isOpen={envOpen} onClose={() => setEnvOpen(false)} title="环境状态" style={{ width: 520 }}>
        <div style={{ padding: 12, fontSize: 12.4 }}>
          {envError ? <Callout intent="danger" compact>{envError}</Callout> : null}
          {!envData && !envError ? <Spinner size={16} /> : null}
          {envData ? (
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>
                <tr><td style={{ padding: 3, color: '#566069' }}>Python</td><td>{envData.python}</td></tr>
                <tr><td style={{ padding: 3, color: '#566069' }}>解释器</td><td className="cf-mono" style={{ fontSize: 11 }}>{envData.executable}</td></tr>
                <tr><td style={{ padding: 3, color: '#566069' }}>系统</td><td>{envData.platform}</td></tr>
                <tr><td style={{ padding: 3, color: '#566069' }}>计算设备</td><td>{envData.device.device}（{envData.device.reason}）</td></tr>
                <tr><td style={{ padding: 3, color: '#566069' }}>磁盘</td><td>剩余 {envData.disk.free_gb} GB / 共 {envData.disk.total_gb} GB；results 占用 {envData.disk.results_gb} GB</td></tr>
                <tr><td style={{ padding: 3, color: '#566069' }}>前端构建</td><td>{envData.web_built ? '已构建' : '缺少 web/dist'}</td></tr>
                <tr><td style={{ padding: 3, color: '#566069' }}>队列</td><td>
                  {Object.entries(envData.queue).map(([key, value]) => `${key} ${value}`).join(' · ')}
                </td></tr>
                <tr><td style={{ padding: 3, color: '#566069' }}>数据目录</td><td className="cf-mono" style={{ fontSize: 11 }}>{envData.paths.results}</td></tr>
              </tbody>
            </table>
          ) : null}
          {envData ? (
            <div style={{ marginTop: 8 }}>
              <div className="cf-sub">关键依赖版本</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 4 }}>
                {Object.entries(envData.versions).map(([key, value]) => (
                  <Tag key={key} minimal>{key} {String(value || '未安装')}</Tag>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </Dialog>

      <Dialog isOpen={createOpen} onClose={() => setCreateOpen(false)} title="新建项目" style={{ width: 380 }}>
        <div style={{ padding: 12 }}>
          <p className="cf-sub">项目用于分开管理数据集登记与任务记录；不会移动或删除已有文件。</p>
          <InputGroup placeholder="项目名称，例如：造血分化"
                      value={newProjectName}
                      onChange={(event) => setNewProjectName(event.currentTarget.value)} />
          <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end', gap: 6 }}>
            <Button onClick={() => setCreateOpen(false)}>取消</Button>
            <Button intent="primary" disabled={!newProjectName.trim()} onClick={async () => {
              try {
                const result = await api.createProject(newProjectName.trim());
                setCreateOpen(false);
                notify('ok', `已创建并切换到项目：${result.project.name}`);
                refresh();
              } catch (exc) {
                notify('bad', `创建失败：${exc}`);
              }
            }}>创建</Button>
          </div>
        </div>
      </Dialog>

      <Dialog isOpen={manageOpen} onClose={() => setManageOpen(false)} title="项目管理" style={{ width: 560 }}>
        <div style={{ padding: 12, fontSize: 12.4 }}>
          <p className="cf-sub">
            归档后项目从下拉框隐藏，但数据与任务保留；删除只允许空项目，且不能是默认或当前项目。
          </p>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr>
                <th style={{ textAlign: 'left', padding: 4 }}>名称</th>
                <th style={{ textAlign: 'left', padding: 4 }}>状态</th>
                <th style={{ textAlign: 'left', padding: 4 }}>操作</th>
              </tr>
            </thead>
            <tbody>
              {(meta.projects || []).map((project) => (
                <tr key={project.id} style={{ borderTop: '1px solid #eef1f4' }}>
                  <td style={{ padding: 4 }}>
                    {project.name}
                    {project.id === meta.active_project?.id ? <Tag minimal style={{ marginLeft: 6 }}>当前</Tag> : null}
                  </td>
                  <td style={{ padding: 4 }}>
                    {project.archived ? <Tag minimal intent="warning">已归档</Tag> : <Tag minimal>使用中</Tag>}
                  </td>
                  <td style={{ padding: 4 }}>
                    <Button small minimal disabled={project.archived || project.id === meta.active_project?.id}
                            onClick={async () => {
                              try {
                                await api.activateProject(project.id);
                                refresh();
                              } catch (exc) {
                                notify('bad', `切换失败：${exc}`);
                              }
                            }}>切换</Button>
                    <Button small minimal onClick={async () => {
                      try {
                        await api.archiveProject(project.id, !project.archived);
                        refresh();
                      } catch (exc) {
                        notify('bad', `归档操作失败：${exc}`);
                      }
                    }}>{project.archived ? '恢复' : '归档'}</Button>
                    <Button small minimal intent="danger" onClick={async () => {
                      if (!window.confirm(`删除项目「${project.name}」？\n\n只允许删除空项目；数据与任务不会被删除。`)) return;
                      try {
                        const result = await api.deleteProject(project.id);
                        notify('ok', result.message || '已删除');
                        refresh();
                      } catch (exc) {
                        notify('bad', `${exc}`);
                      }
                    }}>删除</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Dialog>
    </>
  );
}
