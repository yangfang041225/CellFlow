import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Button, Callout, FormGroup, HTMLSelect, InputGroup, NumericInput, Spinner, Switch, Tag,
} from '@blueprintjs/core';
import { api, Dataset, Inspect, Meta, Run } from '../services/api';
import { createTracker, isAbortError } from '../services/async';
import {
  AUTO_GROUP, Declaration, datasetVersionKey, readDraftForVersion, saveDraftForVersion,
} from '../services/context';
import { evaluateMethods, MethodGate } from '../services/precheck';

type Props = {
  meta: Meta; dataset?: Dataset; refresh: () => void;
  notify: (kind: 'ok' | 'warn' | 'bad', text: string) => void;
  declaration: Declaration;
  onDeclarationChange: (patch: Partial<Declaration>) => void;
};

const TEXT_FIELDS: { key: string; label: string; hint: string; placeholder?: string }[] = [
  { key: 'species', label: '物种 species', hint: '写进报告；不会自动推断。' },
  { key: 'sample_key', label: '样本列 sample_key', hint: '供体/样本列；未指定时细胞不是独立生物学重复。' },
];

/** 服务端 inspect 返回的默认配置；声明是数据级状态，不放进参数草稿。 */
function baseConfig(data: Inspect): Record<string, any> {
  const config: Record<string, any> = { ...(data.config || {}) };
  delete config.raw_x_confirmed;
  delete config.raw_su_confirmed;
  return config;
}

function cleanDraft(value: Record<string, any> | null): Record<string, any> {
  if (!value || typeof value !== 'object') return {};
  const config: Record<string, any> = { ...value };
  delete config.raw_x_confirmed;
  delete config.raw_su_confirmed;
  return config;
}

export function ModelView({
  meta, dataset, refresh, notify, declaration, onDeclarationChange,
}: Props) {
  const version = datasetVersionKey(dataset);
  const [inspect, setInspect] = useState<Inspect | null>(null);
  const [inspectState, setInspectState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [inspectError, setInspectError] = useState('');
  const [method, setMethod] = useState('qc');
  const [runs, setRuns] = useState<Run[]>([]);
  const [busy, setBusy] = useState(false);
  const [config, setConfig] = useState<Record<string, any>>({});
  const configRef = useRef<Record<string, any>>(config);
  configRef.current = config;
  /** 已经加载完草稿的数据版本键；为空表示不能把 config 写回存储。 */
  const loadedKeyRef = useRef('');
  const inspectTracker = useRef(createTracker()).current;
  const runsTracker = useRef(createTracker()).current;

  // 预检请求：切换数据/声明时取消旧请求；旧响应晚到也不能覆盖新数据。
  useEffect(() => {
    const controller = new AbortController();
    const token = inspectTracker.begin();
    // 切换前先把当前编辑中的草稿写回“旧的数据版本键”，
    // 否则新数据的草稿还没加载完，旧 config 会被写到新数据集名下（F31）。
    if (loadedKeyRef.current) {
      saveDraftForVersion(loadedKeyRef.current, configRef.current);
      loadedKeyRef.current = '';
    }
    if (!dataset) {
      setInspect(null);
      setInspectState('idle');
      setInspectError('');
      setConfig({});
      return () => controller.abort();
    }
    // 同一数据集重新预检（如切换声明）时保留旧结果避免表单闪烁；切换数据集时立即清空。
    setInspect((previous) => (previous && previous.path === dataset.path ? previous : null));
    setInspectState('loading');
    setInspectError('');
    api.inspect(dataset.path, declaration.rawX, declaration.rawSU, controller.signal)
      .then((data) => {
        if (!inspectTracker.current(token)) return;
        const merged = { ...baseConfig(data), ...cleanDraft(readDraftForVersion(version)) };
        const gates = evaluateMethods(data.methods, data.facts, merged);
        loadedKeyRef.current = version; // 草稿加载完成后才允许写回
        setInspect(data);
        setInspectState('ready');
        setConfig(merged);
        setMethod((current) => (gates.some((gate) => gate.key === current)
          ? current
          : (gates.find((gate) => gate.ready)?.key || data.methods[0]?.key || 'qc')));
      })
      .catch((exc) => {
        if (!inspectTracker.current(token) || isAbortError(exc)) return;
        setInspectState('error');
        setInspectError(String(exc));
        notify('bad', `模型预检失败：${exc}`);
      });
    return () => controller.abort();
  }, [dataset?.path, version, declaration.rawX, declaration.rawSU]);

  // 草稿：只有“当前版本的草稿确实加载完成”之后才写回，避免覆盖/串数据。
  useEffect(() => {
    if (!version) return;
    if (loadedKeyRef.current !== version) return;
    saveDraftForVersion(version, config);
  }, [config, version]);

  // 离开模型页时把最后的编辑写回当前数据版本。
  useEffect(() => () => {
    if (loadedKeyRef.current) saveDraftForVersion(loadedKeyRef.current, configRef.current);
  }, []);

  // 任务列表按当前数据集限定，避免把别的数据的任务当成“本方法的最近任务”。
  useEffect(() => {
    const controller = new AbortController();
    const token = runsTracker.begin();
    if (!dataset) {
      setRuns([]);
      return () => controller.abort();
    }
    api.runs({ dataset: dataset.path }, controller.signal)
      .then((data) => { if (runsTracker.current(token)) setRuns(data.runs); })
      .catch((exc) => { if (runsTracker.current(token) && !isAbortError(exc)) notify('bad', String(exc)); });
    return () => controller.abort();
  }, [meta, dataset?.path, version]);

  // 渲染时再核对一次数据版本：切换数据集的同一帧不会显示上一份数据的预检结果。
  const currentInspect = inspect && dataset && inspect.path === dataset.path ? inspect : null;
  const methods = currentInspect?.methods || [];
  const obsKeys = currentInspect?.facts.obs_keys || [];

  // 每次相关字段变化都在本地重新预检（同步计算，不会出现旧响应覆盖）。
  const gates = useMemo(
    () => evaluateMethods(methods, currentInspect?.facts, config),
    [currentInspect, config],
  );
  const gateByKey = useMemo(() => {
    const map = new Map<string, MethodGate>();
    gates.forEach((gate) => map.set(gate.key, gate));
    return map;
  }, [gates]);
  const row = useMemo(() => methods.find((item) => item.key === method), [methods, method]);
  const gate = gateByKey.get(method);
  const ready = Boolean(dataset) && inspectState === 'ready' && Boolean(gate?.ready);
  const ordered = useMemo(() => {
    const readyRows = methods.filter((item) => gateByKey.get(item.key)?.ready);
    const blockedRows = methods.filter((item) => !gateByKey.get(item.key)?.ready);
    return [...readyRows, ...blockedRows];
  }, [methods, gateByKey]);
  const active = runs.find(
    (item) => item.method === method && (item.state === 'queued' || item.state === 'running'),
  );
  const set = (key: string, value: any) => setConfig((current) => ({ ...current, [key]: value }));
  const blockReasons = [...(gate?.missing || []), ...(gate?.blockers || [])];
  const blockText = blockReasons.join('；') || '请先补齐前置条件';

  const submit = async () => {
    if (!dataset || !row) return;
    // 提交端与按钮使用同一份 gate 结论，不允许绕过按钮直接提交（F06）。
    const currentGate = gateByKey.get(method);
    if (!currentGate?.ready) {
      const reasons = [...(currentGate?.missing || []), ...(currentGate?.blockers || [])].join('；')
        || '请先补齐前置条件';
      notify('warn', `「${row.name}」前置条件未满足：${reasons}`);
      return;
    }
    setBusy(true);
    try {
      const payload = {
        ...config,
        raw_x_confirmed: declaration.rawX,
        raw_su_confirmed: declaration.rawSU,
      };
      const result = await api.createRun(dataset.path, method, payload);
      notify('ok', `任务 ${result.id.slice(0, 8)} 已在队列中（相同配置的活动任务会被复用）。到「分析结果」查看。`);
      refresh();
      const data = await api.runs({ dataset: dataset.path });
      setRuns(data.runs);
    } catch (exc) {
      notify('bad', `提交失败：${exc}`);
    } finally {
      setBusy(false);
    }
  };

  const numeric = (key: string, label: string, min: number, max: number, step: number, hint: string) => (
    <FormGroup label={label} helperText={hint}>
      <NumericInput
        fill value={Number(config[key] ?? 0)} min={min} max={max}
        stepSize={step} minorStepSize={step / 5} majorStepSize={step * 10}
        onValueChange={(value) => set(key, value)}
      />
    </FormGroup>
  );

  const groupKeyHint = `obs 列：${obsKeys.join('、') || '（尚未返回）'}；填 ${AUTO_GROUP} 表示自动分群。`;
  const currentNeeds = row?.needs || [];
  const labelFor = (need: string, text: string) => (
    currentNeeds.includes(need) ? `${text}（本方法必需）` : text
  );

  return (
    <div>
      <div className="cf-h1">模型分析</div>
      <div className="cf-sub">选择方法、确认前置条件、配置参数并运行。每个方法一个独立任务。</div>

      {!dataset ? <Callout intent="warning">先到「数据」选择一个数据集。</Callout> : null}

      <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 12 }}>
        <div>
          <div className="cf-sec">方法</div>
          <HTMLSelect fill value={method} style={{ height: 30, fontSize: 12.6 }}
                      onChange={(event) => setMethod(event.currentTarget.value)}>
            {ordered.map((item) => (
              <option key={item.key} value={item.key}>
                {gateByKey.get(item.key)?.ready ? '可运行' : '受限'} · {item.name}
              </option>
            ))}
          </HTMLSelect>
          {row ? (
            <div style={{ marginTop: 6 }}>
              <Tag minimal>{row.family}</Tag>
              <Tag minimal intent={row.status === 'ready' ? 'success' : row.status === 'experimental' ? 'warning' : 'none'}>
                {row.status === 'ready' ? '可运行' : row.status === 'experimental' ? '实验接入' : '尚未接入'}
              </Tag>
            </div>
          ) : null}

          <div className="cf-sec">本方法的最近任务</div>
          {(() => {
            const last = runs.find((item) => item.method === method);
            if (!last) return <div className="cf-sub">还没有运行过</div>;
            return (
              <div style={{ fontSize: 12.4 }}>
                <div className="cf-mono">{last.id.slice(0, 8)}</div>
                <Tag minimal intent={last.state === 'completed' ? 'success' : last.state === 'failed' ? 'danger' : 'primary'}>
                  {last.state}
                </Tag>
                {last.error ? <Callout intent="danger" compact style={{ marginTop: 4 }}>{last.error}</Callout> : null}
              </div>
            );
          })()}
        </div>

        <div>
          {inspectState === 'loading' ? <Spinner size={18} /> : null}
          {inspectState === 'error' ? (
            <Callout intent="danger" compact>预检失败：{inspectError}</Callout>
          ) : null}
          {currentInspect && row ? (
            <>
              <div className="cf-sec">{row.name} <span className="h">{row.packages.join('、')}</span></div>
              <Callout compact style={{ fontSize: 12 }}>
                <b>必需输入</b>：{row.needs.join('、') || '无'}<br />
                <b>缺失</b>：{gate?.missing.length ? gate.missing.join('；') : '无（满足）'}
              </Callout>
              {gate?.blockers.length ? (
                <Callout intent="danger" compact style={{ marginTop: 4 }}>
                  运行时/数据限制：{gate.blockers.join('；')}
                </Callout>
              ) : null}
              <div className="cf-sub" style={{ marginTop: 4 }}>
                预检结论：
                {gate
                  ? (gate.ready
                    ? <Tag minimal intent="success">可运行</Tag>
                    : <Tag minimal intent="warning">还缺 {blockReasons.length} 项</Tag>)
                  : '预检中…'}
                {' '}按当前表单值实时复算；起点细胞是否存在于数据中由提交后的数据校验确认。
              </div>

              <div className="cf-sec">方法前提 <span className="h">DPT / CellRank 需起点，PAGA 需分组，RegVelo 需先验 GRN</span></div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <FormGroup label={labelFor('root', '起点细胞 root_cell')}
                           helperText="起始细胞在数据中的 ID；与依据同时填写才会通过预检。">
                  <InputGroup value={String(config.root_cell ?? '')}
                              placeholder="例如 cell_001"
                              onChange={(event) => set('root_cell', event.currentTarget.value)} />
                </FormGroup>
                <FormGroup label={labelFor('root', '起点依据 root_reason')}
                           helperText="为什么选它作为起点（实验或文献依据）。">
                  <InputGroup value={String(config.root_reason ?? '')}
                              onChange={(event) => set('root_reason', event.currentTarget.value)} />
                </FormGroup>
                <FormGroup label={labelFor('groups', '分组列 group_key')} helperText={groupKeyHint}>
                  <InputGroup value={String(config.group_key ?? '')}
                              list="cf-obs-columns"
                              onChange={(event) => set('group_key', event.currentTarget.value)} />
                </FormGroup>
                <FormGroup label={labelFor('prior_GRN', '先验 GRN 文件 grn_path')}
                           helperText="RegVelo 用 CSV（TF 为行、target 为列），≤10 MiB。">
                  <InputGroup value={String(config.grn_path ?? '')}
                              onChange={(event) => set('grn_path', event.currentTarget.value)} />
                </FormGroup>
                <FormGroup label={labelFor('prior_GRN', 'GRN 来源 grn_source')}
                           helperText="说明这张先验网络来自哪里（物种、方向、基因 ID 与版本）。">
                  <InputGroup value={String(config.grn_source ?? '')}
                              onChange={(event) => set('grn_source', event.currentTarget.value)} />
                </FormGroup>
                <FormGroup label={labelFor('prior_GRN', '先验 GRN 已核查')}
                           helperText="勾选即声明已核对来源、物种、方向与基因 ID。">
                  <Switch checked={config.grn_confirmed === true}
                          label="已核对来源 / 物种 / 方向 / 基因 ID"
                          onChange={(event) => set('grn_confirmed', event.currentTarget.checked)} />
                </FormGroup>
              </div>

              <div className="cf-sec">参数 <span className="h">默认值适用于多数情况；改动会按数据集版本保存草稿</span></div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                <div>
                  {numeric('n_top_genes', '高变基因数 n_top_genes', 10, 10000, 100,
                    '参与降维与速度拟合的基因数；越大越慢。')}
                  {numeric('n_neighbors', '邻居数 n_neighbors', 3, 200, 1,
                    '细胞邻接图邻居数；小数据 10–15 更敏感。')}
                  {numeric('n_pcs', '主成分数 n_pcs', 2, 100, 1, 'PCA 保留的主成分数，通常 20–50。')}
                </div>
                <div>
                  {(method === 'velovi' || method === 'regvelo')
                    ? numeric('max_epochs', '训练轮数 max_epochs', 1, 1000, 10, '神经网络训练轮数；CPU 越慢需按收敛调。')
                    : null}
                  <FormGroup label="速度模型 velocity_model"
                             helperText="scVelo 官方 mode；veloVI 需要独立环境。">
                    <HTMLSelect fill value={config.velocity_model}
                                onChange={(event) => set('velocity_model', event.currentTarget.value)}>
                      {meta.velocity_models.map((item) => (
                        <option key={item.key} value={item.key}>{item.label}</option>
                      ))}
                    </HTMLSelect>
                  </FormGroup>
                  <FormGroup label="计算设备 device"
                             helperText="自动：有 CUDA 用 GPU；纯 CPU 方法用 GPU 不会更快。">
                    <HTMLSelect fill value={config.device}
                                onChange={(event) => set('device', event.currentTarget.value)}>
                      <option value="auto">自动</option>
                      <option value="cpu">CPU</option>
                      <option value="cuda">GPU（CUDA）</option>
                    </HTMLSelect>
                  </FormGroup>
                </div>
              </div>

              <details style={{ marginTop: 4 }}>
                <summary style={{ fontSize: 12.4, color: '#566069', cursor: 'pointer' }}>高级参数与科学输入</summary>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 6 }}>
                  {numeric('seed', '随机种子 seed', 0, 2147483647, 1, '固定种子便于复现。')}
                  {numeric('min_shared_counts', '最小共享计数 min_shared_counts', 1, 1000, 1,
                    '过滤剪接信号过低的基因。')}
                  {numeric('leiden_resolution', '聚类分辨率 leiden_resolution', 0.05, 5, 0.05,
                    '越大群越多。')}
                  <FormGroup label="二维投影">
                    <Switch checked={!!config.use_umap} label="使用 UMAP"
                            onChange={(event) => set('use_umap', event.currentTarget.checked)} />
                  </FormGroup>
                  {numeric('arrow_scale', '箭头显示缩放 arrow_scale', 0.5, 3, 0.1,
                    '只影响图的显示长度，不改速度值或方向。')}
                  {numeric('arrow_density', '箭头密度 arrow_density', 20, 500, 10,
                    '每张图最多画多少条箭头；越小越稀疏。')}
                  {TEXT_FIELDS.map((field) => (
                    <FormGroup
                      key={field.key}
                      label={field.label}
                      helperText={field.hint}>
                      <InputGroup value={String(config[field.key] ?? '')}
                                  placeholder={field.placeholder}
                                  onChange={(event) => set(field.key, event.currentTarget.value)} />
                    </FormGroup>
                  ))}
                </div>
              </details>

              <datalist id="cf-obs-columns">
                {obsKeys.map((key) => <option key={key} value={key} />)}
                <option value={AUTO_GROUP}>自动分群</option>
              </datalist>

              <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                <Switch checked={declaration.rawX} label="X 为原始计数"
                        onChange={(event) => onDeclarationChange({ rawX: event.currentTarget.checked })} />
                <Switch checked={declaration.rawSU} label="剪接层为原始计数"
                        onChange={(event) => onDeclarationChange({ rawSU: event.currentTarget.checked })} />
              </div>
              <div className="cf-sub" style={{ marginBottom: 4 }}>
                声明按数据版本保存（<span className="cf-mono">{version || '未选择'}</span>），与「数据」页共用；
                切换数据集后需要重新确认。
              </div>

              <div style={{ marginTop: 8 }}>
                <Button intent="primary" icon="play" loading={busy}
                        disabled={!ready || busy} onClick={submit}>
                  运行分析
                </Button>
                {gate?.ready ? (
                  <span style={{ marginLeft: 8, fontSize: 11.8, color: '#1d5b3b' }}>前置条件已满足</span>
                ) : (
                  <span style={{ marginLeft: 8, fontSize: 11.8, color: '#7a4a00' }}>
                    前置条件未满足：{blockText}
                  </span>
                )}
                {active ? <span style={{ marginLeft: 8, fontSize: 11.8 }}>该配置已有任务在运行/排队：{active.id.slice(0, 8)}</span> : null}
              </div>
            </>
          ) : null}
          {inspectState === 'ready' && !row ? <Callout compact>没有该方法的信息。</Callout> : null}
        </div>
      </div>
    </div>
  );
}
