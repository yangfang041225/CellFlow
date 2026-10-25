/**
 * 异步请求防护：取消/版本核对的最小工具。
 *
 * 页面切换数据、任务或筛选条件时会并发多个请求；旧请求可能比新请求更晚返回。
 * 每个视图用 createTracker() 取一个递增序号，响应回来时用 current() 核对，
 * 同时用 AbortController 取消已经不关心的请求（fetch 会以 AbortError 结束）。
 */

export function isAbortError(error: unknown): boolean {
  if (!error) return false;
  const name = (error as { name?: string }).name;
  if (name === 'AbortError') return true;
  const message = typeof error === 'string'
    ? error
    : (error as { message?: string }).message;
  return typeof message === 'string' && /abort(ed)?/i.test(message);
}

export type RequestTracker = {
  /** 开始一次新请求，返回它的版本号。 */
  begin: () => number;
  /** 该版本号是否仍是当前请求（旧响应返回 false，应当丢弃）。 */
  current: (token: number) => boolean;
};

export function createTracker(): RequestTracker {
  let sequence = 0;
  return {
    begin: () => {
      sequence += 1;
      return sequence;
    },
    current: (token: number) => token === sequence,
  };
}
