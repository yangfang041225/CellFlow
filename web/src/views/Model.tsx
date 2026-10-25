import { useEffect, useMemo, useState } from 'react';
import {
  Button, Callout, FormGroup, HTMLSelect, InputGroup, NumericInput, Spinner, Switch, Tag,
} from '@blueprintjs/core';
import { api, Dataset, Inspect, Meta, Run } from '../services/api';

type Props = {
  meta: Meta; dataset?: Dataset; refresh: () => void;
  notify: (kind: 'ok' | 'warn' | 'bad', text: string) => void;
};

const TEXT_FIELDS: { key: string; label: string; hint: string; placeholder?: string }[] = [
  { key: 'root_cell', label: '起点细胞 root_cell', hint: 'DPT / 命运分析需要：起始细胞在 obs 中的名字。', placeholder: '例如 cell_001' },
  { key: 'root_reason', label: '起点依据 root_reason', hint: '为什么选它作为起点（实验或文献依据）。' },
  { key: 'group_key', label: '分组列 group_key', hint: 'PAGA / 分群使用哪一列 obs；留空使用自动分群。' },
  { key: 'species', label: '物种 species', hint: '写进报告；不会自动推断。' },
  { key: 'sample_key', label: '样本列 sample_key', hint: '供体/样本列；未指定时细胞不是独立生物学重复。' },
  { key: 'grn_path', label: '先验 GRN 文件 grn_path', hint: 'RegVelo 用 CSV（TF 为行、target 为列），≤10 MiB。' },
  { key: 'grn_source', label: 'GRN 来源 grn_source', hint: '说明这张先验网络来自哪里。' },
];

function draftKey(dataset?: Dataset) {
  return 'cf.draft.' + (dataset?.id || 'none');
}

function loadDraft(dataset?: Dataset, fallback: Record<string, any> = {}) {
  try {
    const raw = localStorage.getItem(draftKey(dataset));
    if (!raw) return fallback;
    return { ...fallback, ...JSON.parse(raw) };
  } catch {
    return fallback;
  }
}

export function ModelView({ meta, dataset, refresh, notify }: Props) {
  const [inspect, setInspect] = useState<Inspect | null>(null);
  const [method, setMethod] = useState('qc');
  const [runs, setRuns] = useState<Run[]>([]);
  const [busy, setBusy] = useState(false);
  const [config, setConfig] = useState<Record<string, any>>({});
  const [rawX, setRawX] = useState(localStorage.getItem('cf.raw_x') === '1');
  const [rawSU, setRawSU] = useState(localStorage.getItem('cf.raw_su') === '1');

  useEffect(() => {
    if (!dataset) return;
    api.inspect(dataset.path, rawX, rawSU).then((data) => {
      setInspect(data);
      setConfig(loadDraft(dataset, { ...data.config }) as Record<string, any>);
      setMethod((current) => (data.methods.some((row) => row.key === current) ? current : (data.methods.find((row) => row.can_run)?.key || data.methods[0]?.key || 'qc')));
    }).catch((exc) => notify('bad', String(exc)));
  }, [dataset?.path, rawX, rawSU]);

  useEffect(() => { api.runs().then((data) => setRuns(data.runs)); }, [meta]);

  useEffect(() => {
    if (dataset && Object.keys(config).length) {
      localStorage.setItem(draftKey(dataset), JSON.stringify(config));
    }
  }, [config, dataset?.path]);

  const methods = inspect?.methods || [];
  const row = useMemo(() => methods.find((item) => item.key === method), [methods, method]);
  const ready = methods.filter((item) => item.can_run);
  const blocked = methods.filter((item) => !item.can_run);
  const set = (key: string, value: any) => setConfig((current) => ({ ...current, [key]: value }));
  const active = runs.find((item) => item.method === method && (item.state === 'queued' || item.state === 'running'));

  const run = async () => {
    if (!dataset || !row) return;
    setBusy(true);
    try {
      const payload = {
        ...config,
        raw_x_confirmed: rawX,
        raw_su_confirmed: rawSU,
      };
      const result = await api.createRun(dataset.path, method, payload);
      notify('ok', `任务 ${result.id.slice(0, 8)} 已在队列中（相同配置的活动任务会被复用）。到「分析结果」查看。`);
      refresh();
      const data = await api.runs();
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
            {[...ready, ...blocked].map((item) => (
              <option key={item.key} value={item.key}>
                {item.can_run ? '可运行' : '受限'} · {item.name}
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
          {inspect && row ? (
            <>
              <div className="cf-sec">{row.name} <span className="h">{row.packages.join('、')}</span></div>
              <Callout compact style={{ fontSize: 12 }}>
                <b>必需输入</b>：{row.needs.join('、') || '无'}<br />
                <b>缺失</b>：{(row.missing || []).length ? row.missing.join('；') : '无（满足）'}
              </Callout>
              {(row.blockers || []).length ? (
                <Callout intent="warning" compact style={{ marginTop: 4 }}>{row.blockers.join('；')}</Callout>
              ) : null}

              <div className="cf-sec">参数 <span className="h">默认值适用于多数情况；改动会按数据集保存草稿</span></div>
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
                    <FormGroup key={field.key} label={field.label} helperText={field.hint}>
                      <InputGroup value={String(config[field.key] ?? '')}
                                  placeholder={field.placeholder}
                                  onChange={(event) => set(field.key, event.currentTarget.value)} />
                    </FormGroup>
                  ))}
                </div>
              </details>

              <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
                <Switch checked={rawX} label="X 为原始计数"
                        onChange={(event) => setRawX(event.currentTarget.checked)} />
                <Switch checked={rawSU} label="剪接层为原始计数"
                        onChange={(event) => setRawSU(event.currentTarget.checked)} />
              </div>

              <div style={{ marginTop: 8 }}>
                <Button intent="primary" icon="play" loading={busy}
                        disabled={!row.can_run || busy} onClick={run}>
                  运行分析
                </Button>
                {active ? <span style={{ marginLeft: 8, fontSize: 11.8 }}>该配置已有任务在运行/排队：{active.id.slice(0, 8)}</span> : null}
                {!row.can_run ? (
                  <span style={{ marginLeft: 8, fontSize: 11.8, color: '#7a4a00' }}>
                    前置条件未满足：先补齐上方缺失项
                  </span>
                ) : null}
              </div>
            </>
          ) : <Spinner size={18} />}
        </div>
      </div>
    </div>
  );
}
