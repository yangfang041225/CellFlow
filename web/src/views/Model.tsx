import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Button, Callout, Dialog, FormGroup, HTMLSelect, InputGroup, NumericInput, Spinner, Switch, Tag,
} from '@blueprintjs/core';
import { api, Dataset, Inspect, Meta, Run } from '../services/api';
import { createTracker, isAbortError } from '../services/async';
import { AUTO_GROUP, Declaration, datasetVersionKey } from '../services/context';
import { evaluateMethods, MethodGate } from '../services/precheck';
import { MethodCard, methodCard } from '../content/methods';
import {
  buildConfigFile, buildSubmitPayload, CONFIG_FILE_VERSION, MODE_BY_METHOD,
  PARAMS, ParamSpec, parseConfigFile, ParsedConfigFile, pick, readMethodDraft,
  saveMethodDraft, SHARED_KEYS, scienceKeysFor, paramKeysFor,
} from '../services/modelConfig';

type Props = {
  meta: Meta; dataset?: Dataset; refresh: () => void;
  notify: (kind: 'ok' | 'warn' | 'bad', text: string) => void;
  declaration: Declaration;
  onDeclarationChange: (patch: Partial<Declaration>) => void;
};

type ImportResult = ParsedConfigFile;

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

function methodTitle(item?: { key: string; name: string } | null): string {
  if (!item) return '';
  return methodCard(item.key)?.name || item.name;
}

function CardTitle(props: { children: ReactNode }) {
  return (
    <div style={{ fontWeight: 600, fontSize: 12.3, margin: '2px 0 2px 0', color: '#1d2329' }}>
      {props.children}
    </div>
  );
}

function CardList(props: { items: string[] }) {
  return (
    <ul className="cf-card-list">
      {props.items.map((item) => <li key={item}>{item}</li>)}
    </ul>
  );
}

/** F38：方法说明短卡。文案来自 cellflow/method_content.py（逐字镜像，见 content/methods.ts）。 */
function MethodCardView(props: { card?: MethodCard; gate?: MethodGate; planned: boolean }) {
  const { card, gate, planned } = props;
  if (!card) return null;
  const summary = (
    <>
      <b style={{ fontSize: 12.8 }}>{card.name}</b>
      <span className="cf-sub cf-mono" style={{ marginLeft: 6 }}>{card.name_en}</span>
      <Tag minimal style={{ marginLeft: 6 }}>{card.family}</Tag>
      {planned ? (
        <Tag minimal intent="warning" style={{ marginLeft: 4 }}>未接入</Tag>
      ) : gate?.ready ? (
        <Tag minimal intent="success" style={{ marginLeft: 4 }}>可运行</Tag>
      ) : (
        <Tag minimal style={{ marginLeft: 4 }}>受限</Tag>
      )}
    </>
  );
  const body = (
    <>
      <div className="cf-method-grid">
        <div>
          <CardTitle>解决什么</CardTitle>
          <div style={{ fontSize: 12.3, lineHeight: 1.7 }}>{card.question}</div>
        </div>
        <div>
          <CardTitle>需要什么数据</CardTitle>
          <CardList items={card.inputs_required} />
          {card.inputs_optional.length ? (
            <details>
              <summary className="cf-sub" style={{ cursor: 'pointer' }}>
                可选输入（{card.inputs_optional.length} 项，有则更好）
              </summary>
              <CardList items={card.inputs_optional} />
            </details>
          ) : null}
        </div>
        <div>
          <CardTitle>不适用</CardTitle>
          <div style={{ fontSize: 12.3, lineHeight: 1.7 }}>{card.not_question}</div>
          <CardList items={card.boundaries} />
        </div>
        <div>
          <CardTitle>失败下一步</CardTitle>
          <ul className="cf-card-list">
            {gate?.blockers?.length ? (
              <li>先解决运行限制：{gate.blockers.join('；')}。</li>
            ) : null}
            {gate?.missing?.length ? (
              <li>补齐科学前提（见「前置条件」）：{gate.missing.join('；')}。</li>
            ) : null}
            <li>数据上限：{card.tech.data_limit}</li>
            <li>{card.tech.status_note}</li>
            <li>失败时看「分析结果 → 运行记录」里的真实报错；本程序不会自动换方法，也不会替换成别的模型。</li>
          </ul>
        </div>
      </div>
      <details style={{ marginTop: 2 }}>
        <summary className="cf-sub" style={{ cursor: 'pointer' }}>本程序实际产出 / 明确不产出 / 主要假设 / 环境</summary>
        <div className="cf-method-grid">
          <div>
            <CardTitle>本程序会产出</CardTitle>
            {card.we_output.length ? <CardList items={card.we_output} /> : <div className="cf-sub">尚未接入，不产出任何结果。</div>}
          </div>
          <div>
            <CardTitle>明确不产出</CardTitle>
            <CardList items={card.we_not_output} />
          </div>
          <div>
            <CardTitle>主要假设</CardTitle>
            <CardList items={card.assumptions} />
          </div>
          <div>
            <CardTitle>环境 / 设备</CardTitle>
            <div className="cf-sub cf-mono" style={{ fontSize: 11.2 }}>{card.tech.env}</div>
            <div className="cf-sub">{card.tech.device}</div>
          </div>
        </div>
      </details>
    </>
  );
  return planned ? (
    <details className="cf-method-card">
      <summary style={{ cursor: 'pointer' }}>{summary}</summary>
      <div style={{ marginTop: 6 }}>{body}</div>
    </details>
  ) : (
    <details className="cf-method-card" open>
      <summary style={{ cursor: 'pointer' }}>{summary}</summary>
      <div style={{ marginTop: 6 }}>{body}</div>
    </details>
  );
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
  const [exportOpen, setExportOpen] = useState(false);
  const [exportText, setExportText] = useState('');
  const [importOpen, setImportOpen] = useState(false);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [copied, setCopied] = useState(false);
  const configRef = useRef<Record<string, any>>(config);
  configRef.current = config;
  /** 已经加载完草稿的数据版本键；为空表示不能把 config 写回存储。 */
  const loadedKeyRef = useRef('');
  /** 当前 config 属于哪个方法的草稿（切换方法时先写回旧方法）。 */
  const draftMethodRef = useRef(method);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const inspectTracker = useRef(createTracker()).current;
  const runsTracker = useRef(createTracker()).current;

  // 预检请求：切换数据/声明时取消旧请求；旧响应晚到也不能覆盖新数据。
  useEffect(() => {
    const controller = new AbortController();
    const token = inspectTracker.begin();
    // 切换前先把当前编辑中的草稿写回“旧的数据版本键 + 旧方法”，
    // 否则新数据的草稿还没加载完，旧 config 会被写到新数据集名下（F31）。
    if (loadedKeyRef.current) {
      saveMethodDraft(loadedKeyRef.current, draftMethodRef.current, configRef.current);
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
        const base = baseConfig(data);
        const baseGates = evaluateMethods(data.methods, data.facts, base);
        const keep = baseGates.some((gate) => gate.key === method) ? method : '';
        const nextMethod = keep
          || baseGates.find((gate) => gate.ready)?.key
          || data.methods[0]?.key
          || 'qc';
        // F29：草稿按方法隔离——只加载当前方法自己的草稿。
        const stored = cleanDraft(readMethodDraft(version, nextMethod));
        draftMethodRef.current = nextMethod;
        loadedKeyRef.current = version; // 草稿加载完成后才允许写回
        setInspect(data);
        setInspectState('ready');
        setMethod(nextMethod);
        setConfig({ ...base, ...stored });
      })
      .catch((exc) => {
        if (!inspectTracker.current(token) || isAbortError(exc)) return;
        setInspectState('error');
        setInspectError(String(exc));
        notify('bad', `模型预检失败：${exc}`);
      });
    return () => controller.abort();
  }, [dataset?.path, version, declaration.rawX, declaration.rawSU]);

  // 草稿：只有“当前版本的草稿确实加载完成”之后才写回，且写到 config 实际所属的方法上。
  useEffect(() => {
    if (!version) return;
    if (loadedKeyRef.current !== version) return;
    saveMethodDraft(version, draftMethodRef.current, config);
  }, [config, version]);

  // 离开模型页时把最后的编辑写回当前数据版本。
  useEffect(() => () => {
    if (loadedKeyRef.current) {
      saveMethodDraft(loadedKeyRef.current, draftMethodRef.current, configRef.current);
    }
  }, []);

  // 任务列表按当前数据集限定，避免把别的数据的任务当成“本方法的最近任务”。
  useEffect(() => {
    const controller = new AbortController();
    const token = runsTracker.begin();
    if (!dataset) {
      setRuns([]);
      return () => controller.abort();
    }
    api.runs({ dataset: dataset.path, limit: 200 }, controller.signal)
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
  const card = methodCard(method);
  const ready = Boolean(dataset) && inspectState === 'ready' && Boolean(gate?.ready);
  const ordered = useMemo(() => {
    const readyRows = methods.filter((item) => gateByKey.get(item.key)?.ready);
    const blockedRows = methods.filter((item) => !gateByKey.get(item.key)?.ready);
    return [...readyRows, ...blockedRows];
  }, [methods, gateByKey]);
  // F38：已接入的方法按科研问题分组；计划方法单独折叠，不混在下拉里。
  const implementedRows = useMemo(
    () => ordered.filter((item) => gateByKey.get(item.key)?.status !== 'planned'),
    [ordered, gateByKey],
  );
  const plannedRows = useMemo(
    () => ordered.filter((item) => gateByKey.get(item.key)?.status === 'planned'),
    [ordered, gateByKey],
  );
  const familyGroups = useMemo(() => {
    const groups: { family: string; rows: typeof implementedRows }[] = [];
    for (const item of implementedRows) {
      const family = item.family || '其他';
      const found = groups.find((group) => group.family === family);
      if (found) found.rows.push(item);
      else groups.push({ family, rows: [item] });
    }
    return groups;
  }, [implementedRows]);
  const active = runs.find(
    (item) => item.method === method && (item.state === 'queued' || item.state === 'running'),
  );
  const set = (key: string, value: any) => setConfig((current) => ({ ...current, [key]: value }));
  const blockReasons = [...(gate?.missing || []), ...(gate?.blockers || [])];
  const blockText = blockReasons.join('；') || '请先补齐前置条件';

  // F29：只把本方法实际读取的参数交给后端；被忽略的键不提交，让服务端默认值生效。
  const submitPayload = buildSubmitPayload(method, config, declaration);
  const scienceKeys = scienceKeysFor(method, config);
  const paramKeys = paramKeysFor(method, config);
  const advancedKeys = paramKeys.filter((key) => PARAMS[key]?.advanced);
  const shownParamKeys = paramKeys.filter((key) => !PARAMS[key]?.advanced);
  const needs = row?.needs || [];
  const requiredNote = (key: string): string => {
    const need = PARAMS[key]?.need;
    return need && needs.includes(need) ? '（本方法必需）' : '';
  };
  const groupKeyHint = `obs 列：${obsKeys.join('、') || '（尚未返回）'}；填 ${AUTO_GROUP} 表示自动分群。`;
  const velocityOptions = (meta.velocity_models || [])
    .filter((item) => item.key !== 'velovi')
    .map((item) => ({ value: item.key, label: item.label }));
  const velocityStale = method === 'auto'
    && String(config.velocity_model || '') === 'velovi';

  const switchMethod = (next: string) => {
    if (!next || next === method) return;
    if (loadedKeyRef.current && loadedKeyRef.current === version && currentInspect) {
      // 先把旧方法写回它自己的草稿，再加载目标方法的草稿（F29：按方法隔离）。
      saveMethodDraft(version, draftMethodRef.current, configRef.current);
      const base = baseConfig(currentInspect);
      const carried = pick(configRef.current, SHARED_KEYS);
      const stored = cleanDraft(readMethodDraft(version, next));
      draftMethodRef.current = next;
      setMethod(next);
      setConfig({ ...base, ...carried, ...stored });
      return;
    }
    draftMethodRef.current = next;
    setMethod(next);
  };

  const submit = async () => {
    if (!dataset || !row) return;
    // 提交端与按钮使用同一份 gate 结论，不允许绕过按钮直接提交（F06）。
    const currentGate = gateByKey.get(method);
    if (!currentGate?.ready) {
      const reasons = [...(currentGate?.missing || []), ...(currentGate?.blockers || [])].join('；')
        || '请先补齐前置条件';
      notify('warn', `「${methodTitle(row)}」前置条件未满足：${reasons}`);
      return;
    }
    setBusy(true);
    try {
      const result = await api.createRun(dataset.path, method, submitPayload);
      notify('ok', `任务 ${result.id.slice(0, 8)} 已在队列中（相同配置的活动任务会被复用）。到「分析结果」查看。`);
      refresh();
      const data = await api.runs({ dataset: dataset.path, limit: 200 });
      setRuns(data.runs);
    } catch (exc) {
      notify('bad', `提交失败：${exc}`);
    } finally {
      setBusy(false);
    }
  };

  // ---- F29：版本化配置 JSON 导入 / 导出
  const openExport = () => {
    if (!currentInspect || !row) return;
    const file = buildConfigFile({
      method,
      methodName: methodTitle(row),
      dataset: { id: dataset?.id, label: dataset?.label, versionKey: version },
      config,
      declaration,
      precheck: {
        ready: Boolean(gate?.ready),
        missing: gate?.missing || [],
        blockers: gate?.blockers || [],
      },
    });
    setExportText(JSON.stringify(file, null, 2));
    setCopied(false);
    setExportOpen(true);
  };

  const downloadExport = () => {
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    const name = `cellflow-config-${method}-${stamp}.json`;
    try {
      const blob = new Blob([exportText], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = name;
      anchor.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 4000);
      notify('ok', `已导出 ${name}`);
    } catch (exc) {
      notify('bad', `导出失败：${exc}`);
    }
  };

  const copyExport = async () => {
    try {
      await navigator.clipboard.writeText(exportText);
      setCopied(true);
      notify('ok', '配置 JSON 已复制到剪贴板');
    } catch {
      notify('warn', '剪贴板不可用：请在文本框里手动全选复制');
    }
  };

  const handleImportFile = async (file: File) => {
    const text = await file.text();
    const parsed = parseConfigFile(text, {
      knownMethods: methods.map((item) => item.key),
      implemented: methods.filter((item) => item.status !== 'planned').map((item) => item.key),
      currentDataset: dataset ? { id: dataset.id, label: dataset.label } : undefined,
    });
    setImportResult(parsed);
    if (parsed.ok && parsed.method && parsed.params) {
      const base = currentInspect ? baseConfig(currentInspect) : {};
      const carried = pick(configRef.current, SHARED_KEYS);
      draftMethodRef.current = parsed.method;
      setMethod(parsed.method);
      setConfig({ ...base, ...carried, ...parsed.params });
      notify('ok', `已导入「${methodTitle({ key: parsed.method, name: parsed.methodName || parsed.method })}」的 ${Object.keys(parsed.params).length} 项参数`);
    } else {
      notify('bad', `配置未导入：${parsed.errors[0] || '校验未通过'}`);
    }
    setImportOpen(true);
  };

  /** 渲染一个参数控件（F29：控件由能力表决定，不在这里写方法名 if）。 */
  const renderField = (key: string) => {
    const spec: ParamSpec | undefined = PARAMS[key];
    if (!spec) return null;
    const label = `${spec.label}${requiredNote(key)}`;
    let hint = key === 'group_key' ? groupKeyHint : spec.hint;
    if (method === 'auto' && key === 'min_shared_counts') {
      hint += '（一键分析只在数据含剪接层时使用）';
    }
    if (method === 'auto' && key === 'velocity_model') {
      hint += '（数据含剪接层时才执行速度推断）';
    }
    if (method === 'auto' && key === 'cellrank_n_states') {
      hint += '（仅在有速度或起点时执行命运分析）';
    }
    if (method === 'auto' && key === 'max_iter') {
      hint += '（仅 velocity_model=dynamical 时使用）';
    }
    if (spec.kind === 'bool') {
      return (
        <FormGroup key={key} label={label} helperText={hint}>
          <Switch checked={config[key] === true}
                  label={key === 'grn_confirmed' ? '已核对来源 / 物种 / 方向 / 基因 ID' : '启用'}
                  onChange={(event) => set(key, event.currentTarget.checked)} />
        </FormGroup>
      );
    }
    if (spec.kind === 'select') {
      const options = key === 'velocity_model' && velocityOptions.length
        ? velocityOptions
        : (spec.options || []);
      const current = options.some((item) => item.value === String(config[key] ?? ''))
        ? String(config[key])
        : (options[0]?.value || '');
      return (
        <FormGroup key={key} label={label} helperText={hint}>
          <HTMLSelect fill value={current} onChange={(event) => set(key, event.currentTarget.value)}>
            {options.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </HTMLSelect>
        </FormGroup>
      );
    }
    if (spec.kind === 'int' || spec.kind === 'float') {
      const fallback = spec.min as number;
      const value = Number(config[key]);
      const shown = Number.isFinite(value) ? value : fallback;
      const step = spec.step || (spec.kind === 'int' ? 1 : 0.1);
      return (
        <FormGroup key={key} label={label} helperText={hint}>
          <NumericInput
            fill value={shown} min={spec.min} max={spec.max}
            stepSize={step} minorStepSize={step / 5} majorStepSize={step * 10}
            onValueChange={(next) => { if (Number.isFinite(next)) set(key, next); }}
          />
        </FormGroup>
      );
    }
    return (
      <FormGroup key={key} label={label} helperText={hint}>
        <InputGroup value={String(config[key] ?? '')}
                    placeholder={spec.placeholder}
                    list={key === 'group_key' ? 'cf-obs-columns' : undefined}
                    onChange={(event) => set(key, event.currentTarget.value)} />
      </FormGroup>
    );
  };

  return (
    <div>
      <div className="cf-h1">模型分析</div>
      <div className="cf-sub">选择方法、确认前置条件、配置参数并运行。每个方法一个独立任务，参数草稿按方法分别保存。</div>

      {!dataset ? <Callout intent="warning">先到「数据」选择一个数据集。</Callout> : null}

      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', margin: '6px 0' }}>
        <Button small minimal icon="document" onClick={openExport} disabled={!currentInspect || !row}>
          导出配置 JSON
        </Button>
        <Button small minimal icon="import" onClick={() => fileRef.current?.click()}
                disabled={!currentInspect}>
          导入配置 JSON
        </Button>
        <input ref={fileRef} type="file" accept=".json,application/json" style={{ display: 'none' }}
               onChange={(event) => {
                 const file = event.target.files?.[0];
                 event.target.value = '';
                 if (file) void handleImportFile(file);
               }} />
        <span className="cf-sub">配置版本 v{CONFIG_FILE_VERSION}；导入先校验再套用，数据声明不从文件继承</span>
      </div>

      <div className="cf-model-grid">
        <div>
          <div className="cf-sec">方法 <span className="h">按科研问题分组</span></div>
          <HTMLSelect fill value={method} style={{ height: 30, fontSize: 12.6 }}
                      onChange={(event) => switchMethod(event.currentTarget.value)}>
            {familyGroups.map((group) => (
              <optgroup key={group.family} label={group.family}>
                {group.rows.map((item) => (
                  <option key={item.key} value={item.key}>
                    {gateByKey.get(item.key)?.ready ? '可运行' : '受限'} · {methodTitle(item)}
                  </option>
                ))}
              </optgroup>
            ))}
            {gate?.status === 'planned' ? (
              <option value={method}>{methodTitle(row)}（未接入）</option>
            ) : null}
          </HTMLSelect>
          {row ? (
            <div style={{ marginTop: 6, display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
              <Tag minimal>{row.family}</Tag>
              <Tag minimal intent={gate?.status === 'ready' ? 'success' : gate?.status === 'experimental' ? 'warning' : 'none'}>
                {gate?.status === 'ready' ? '可运行' : gate?.status === 'experimental' ? '实验接入' : '尚未接入'}
              </Tag>
              <span className="cf-sub cf-mono" style={{ fontSize: 11 }}>{card?.name_en}</span>
            </div>
          ) : null}

          {plannedRows.length ? (
            <details style={{ marginTop: 8 }}>
              <summary style={{ cursor: 'pointer', fontSize: 12.4, color: '#566069' }}>
                计划方法（尚未接入）· {plannedRows.length} 个
              </summary>
              <div style={{ marginTop: 4 }}>
                {plannedRows.map((item) => (
                  <div key={item.key} style={{ display: 'flex', gap: 6, alignItems: 'center', padding: '2px 0' }}>
                    <span style={{ fontSize: 12.2 }}>{methodTitle(item)}</span>
                    <Tag minimal intent="warning">未接入</Tag>
                    <Button small minimal style={{ marginLeft: 'auto' }}
                            onClick={() => switchMethod(item.key)}>说明</Button>
                  </div>
                ))}
                <div className="cf-sub" style={{ marginTop: 2 }}>
                  这些方法只有注册信息与适配计划，没有可调用代码；点击「说明」可看它解决什么问题、需要什么数据。
                </div>
              </div>
            </details>
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
              <div className="cf-sec">
                方法说明
                <span className="h">解决什么 / 需要什么数据 / 不适用 / 失败下一步</span>
              </div>
              <MethodCardView card={card} gate={gate} planned={gate?.status === 'planned'} />

              <div className="cf-sec">
                {methodTitle(row)}
                <span className="h">{row.packages.join('、')}</span>
              </div>
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

              <div className="cf-sec">科学输入 <span className="h">写进 config；标「本方法必需」的项不满足时按钮禁用</span></div>
              <div className="cf-form-grid">
                {scienceKeys.map((key) => renderField(key))}
              </div>
              {!scienceKeys.length ? (
                <div className="cf-sub">本方法没有额外的科学输入；数据声明见下方开关。</div>
              ) : null}

              <div className="cf-sec">参数 <span className="h">只显示本方法实际读取的参数；默认值适用于多数情况</span></div>
              {shownParamKeys.length ? (
                <div className="cf-form-grid">
                  {shownParamKeys.map((key) => renderField(key))}
                </div>
              ) : (
                <div className="cf-sub">
                  本方法没有可调的计算参数；只有高级参数里的随机种子（以及下方的数据声明）。
                </div>
              )}
              {MODE_BY_METHOD[method] ? (
                <Callout compact style={{ marginTop: 4 }}>
                  <b>scVelo mode</b>：<span className="cf-mono">mode = {MODE_BY_METHOD[method]}</span>
                  （由所选方法决定，独立 scVelo 不接受另选 velocity_model）
                </Callout>
              ) : null}
              {velocityStale ? (
                <Callout intent="warning" compact style={{ marginTop: 4 }}>
                  当前草稿里的速度模型是 veloVI，一键分析不能直接调用它（需独立环境与训练过程）。
                  提交前会按默认 stochastic 发送，或
                  {' '}<Button small minimal onClick={() => set('velocity_model', 'stochastic')}>改为 stochastic</Button>
                </Callout>
              ) : null}
              {advancedKeys.length ? (
                <details style={{ marginTop: 2 }}>
                  <summary style={{ fontSize: 12.4, color: '#566069', cursor: 'pointer' }}>
                    高级参数（{advancedKeys.length} 项：{advancedKeys.map((key) => PARAMS[key].label).join('、')}）
                  </summary>
                  <div className="cf-form-grid" style={{ marginTop: 6 }}>
                    {advancedKeys.map((key) => renderField(key))}
                  </div>
                </details>
              ) : null}

              <datalist id="cf-obs-columns">
                {obsKeys.map((key) => <option key={key} value={key} />)}
                <option value={AUTO_GROUP}>自动分群</option>
              </datalist>

              <div className="cf-sec">数据声明</div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
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

              <details style={{ marginTop: 6 }}>
                <summary className="cf-sub" style={{ cursor: 'pointer' }}>
                  将提交的配置（{Object.keys(submitPayload).length} 项；未列出的键使用服务端默认值）
                </summary>
                <pre className="cf-mono" style={{
                  maxHeight: 190, overflow: 'auto', background: '#fafbfc', border: '1px solid #e7eaee',
                  padding: 6, margin: '4px 0 0', fontSize: 11.2,
                }}>{JSON.stringify(submitPayload, null, 2)}</pre>
              </details>
            </>
          ) : null}
          {inspectState === 'ready' && !row ? <Callout compact>没有该方法的信息。</Callout> : null}
        </div>
      </div>

      <Dialog isOpen={exportOpen} onClose={() => setExportOpen(false)} title="导出配置 JSON"
              style={{ width: 'min(620px, 92vw)' }}>
        <div className="cf-dialog-body" style={{ padding: 12 }}>
          <div className="cf-sub" style={{ marginBottom: 6 }}>
            文件带版本号（v{CONFIG_FILE_VERSION}）、方法与预检摘要；不含任务数据本身。
            原始计数声明单独记录、导入时忽略。
          </div>
          <textarea readOnly value={exportText} spellCheck={false}
                    style={{
                      width: '100%', height: 260, fontFamily: 'ui-monospace, Consolas, monospace',
                      fontSize: 11.4, border: '1px solid #d3d8de', padding: 6, resize: 'vertical',
                    }} />
          <div style={{ marginTop: 8, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            <Button intent="primary" icon="download" onClick={downloadExport}>下载文件</Button>
            <Button icon="clipboard" onClick={() => { void copyExport(); }}>{copied ? '已复制' : '复制'}</Button>
            <Button onClick={() => setExportOpen(false)}>关闭</Button>
          </div>
        </div>
      </Dialog>

      <Dialog isOpen={importOpen} onClose={() => setImportOpen(false)} title="导入配置 JSON"
              style={{ width: 'min(560px, 92vw)' }}>
        <div className="cf-dialog-body" style={{ padding: 12, fontSize: 12.4 }}>
          {importResult?.ok ? (
            <Callout intent="success" compact>
              已导入「{importResult.methodName || importResult.method}」：
              {Object.keys(importResult.params || {}).length} 项参数。
              数据声明没有被导入，请按当前数据重新核实。
            </Callout>
          ) : (
            <Callout intent="danger" compact>配置未导入：文件中有 {importResult?.errors.length || 0} 处问题。</Callout>
          )}
          {importResult?.errors.length ? (
            <ul style={{ margin: '8px 0 0 16px', padding: 0 }}>
              {importResult.errors.map((item) => <li key={item} className="cf-mono" style={{ fontSize: 11.4 }}>{item}</li>)}
            </ul>
          ) : null}
          {importResult?.warnings.length ? (
            <Callout intent="warning" compact style={{ marginTop: 8 }}>
              <ul style={{ margin: 0, padding: '0 0 0 16px' }}>
                {importResult.warnings.map((item) => <li key={item}>{item}</li>)}
              </ul>
            </Callout>
          ) : null}
          <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end', gap: 6 }}>
            <Button onClick={() => setImportOpen(false)}>关闭</Button>
          </div>
        </div>
      </Dialog>
    </div>
  );
}
