/**
 * 下载状态轮询的状态机（与 React、与 fetch 都解耦，便于回归测试）。
 *
 * 修复的缺陷：旧实现用 setInterval(800ms)，任何一次异常都会
 *   停止轮询 + 忘记任务 + 清空界面状态。
 * 于是本机服务只要抖动一次（或重启中的一瞬间），一个仍在后台正常下载的
 * 任务就从界面上“消失”了：用户看到的是失败，实际文件还在下。
 *
 * 现在的语义：
 *   - 暂时失联：保留任务与进度，继续重试，并上报 retry；
 *   - 连续失联超过阈值：停止轮询但保留任务记录（不调用 forget），上报 lost，
 *     下次进入数据页仍可恢复；
 *   - 失败分类为 missing（任务 404）：上报 gone，由调用方决定清除记录；
 *   - 失败分类为 fatal：立即停止并上报 fatal；
 *   - 终止状态：上报 status 后自动停止。
 *
 * 本文件不 import 任何应用模块：失败分类由调用方通过 classifyFailure 注入，
 * 这样状态机既能被 Vite 打包，也能被 tsc 单独编译后用 node --test 直接测试。
 */

export type FailureClass = 'missing' | 'transient' | 'fatal';

export type MonitorEvent<S> =
  | { type: 'status'; status: S }
  | { type: 'retry'; failures: number; error: unknown }
  | { type: 'gone'; error: unknown }
  | { type: 'lost'; failures: number; error: unknown }
  | { type: 'fatal'; error: unknown };

export type DownloadMonitorOptions<S> = {
  fetchStatus: (id: string) => Promise<S>;
  onEvent: (event: MonitorEvent<S>) => void;
  isTerminal: (status: S) => boolean;
  classifyFailure: (error: unknown) => FailureClass;
  /** 轮询间隔（毫秒），默认 800（与旧实现一致）。 */
  intervalMs?: number;
  /** 允许的连续失联次数，默认 5（约 4 秒）。 */
  maxTransientFailures?: number;
  schedule?: (callback: () => void, delay: number) => unknown;
  cancel?: (handle: unknown) => void;
};

export type DownloadMonitor<S> = {
  start: (id: string) => void;
  stop: () => void;
  currentId: () => string;
};

export function createDownloadMonitor<S>(options: DownloadMonitorOptions<S>): DownloadMonitor<S> {
  const {
    fetchStatus,
    onEvent,
    isTerminal,
    classifyFailure,
    intervalMs = 800,
    maxTransientFailures = 5,
    schedule = (callback, delay) => setTimeout(callback, delay),
    cancel = (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  } = options;

  let activeId = '';
  let handle: unknown = null;
  let stopped = false;
  let failures = 0;

  const clear = () => {
    if (handle !== null) {
      cancel(handle);
      handle = null;
    }
  };

  const tick = async () => {
    handle = null;
    if (stopped || !activeId) return;
    const probing = activeId;
    try {
      const status = await fetchStatus(probing);
      if (stopped || probing !== activeId) return;
      failures = 0;
      onEvent({ type: 'status', status });
      if (isTerminal(status)) {
        stopped = true;
        return;
      }
    } catch (error) {
      if (stopped || probing !== activeId) return;
      const kind = classifyFailure(error);
      if (kind === 'missing') {
        stopped = true;
        onEvent({ type: 'gone', error });
        return;
      }
      if (kind === 'fatal') {
        stopped = true;
        onEvent({ type: 'fatal', error });
        return;
      }
      failures += 1;
      if (failures >= maxTransientFailures) {
        stopped = true;
        onEvent({ type: 'lost', failures, error });
        return;
      }
      onEvent({ type: 'retry', failures, error });
    }
    if (stopped || probing !== activeId) return;
    handle = schedule(() => { void tick(); }, intervalMs);
  };

  return {
    start(id: string) {
      clear();
      activeId = id;
      stopped = false;
      failures = 0;
      handle = schedule(() => { void tick(); }, intervalMs);
    },
    stop() {
      stopped = true;
      activeId = '';
      clear();
    },
    currentId() {
      return activeId;
    },
  };
}

/** 只有任务确实不存在时才允许 forget；失联/致命错误都必须保留记录。 */
export function shouldForgetTask(event: MonitorEvent<unknown>): boolean {
  return event.type === 'gone';
}

/** 连续失联的界面提示：说清楚是哪一层，避免用户误以为是数据损坏。 */
export function transientFailureMessage(failures: number): string {
  return `本机服务暂时无响应（第 ${failures} 次），正在自动重试；下载任务仍在后台运行。`;
}

export function lostMessage(failures: number): string {
  return `连续 ${failures} 次读不到下载状态：本机服务可能已失联。`
    + '任务仍在后台运行，稍后重新进入「数据」页会自动接着显示进度。';
}
