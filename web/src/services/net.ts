/**
 * 本机 HTTP 请求层：把「浏览器网络层失败」与「HTTP 错误」明确区分开。
 *
 * 背景（本次下载故障的直接观察）：
 *   fetch() 在本地服务没有监听、连接被拒绝、连接被中途重置、请求被中止时
 *   不会返回 Response，而是直接抛出 TypeError('Failed to fetch')。
 *   旧实现把该异常原样抛到界面，用户只能看到
 *   "TypeError: Failed to fetch" —— 分不清是前端、本机服务还是 NCBI。
 *
 * 本模块只做分类与提示，不改变任何业务语义：
 *   - 中止（AbortError）永远原样抛出，调用方的取消逻辑不受影响；
 *   - 其它网络层异常包装成 ApiRequestError(kind='transport')，带可执行提示；
 *   - HTTP 非 2xx 包装成 ApiRequestError(kind='http')，保留状态码与后端 detail。
 *
 * 本文件不 import 任何其它应用模块：既能被 Vite 打包，也能被单独的
 * tsc 编译后用 node --test 直接做回归测试（见 web/tests/net.test.mjs）。
 */

export type RequestFailureKind = 'transport' | 'http' | 'parse';

export type ApiRequestErrorInfo = {
  kind: RequestFailureKind;
  url: string;
  status?: number;
  /** 后端返回的 detail 或原始异常信息，供日志与诊断使用。 */
  detail?: string;
};

export class ApiRequestError extends Error {
  readonly kind: RequestFailureKind;
  readonly url: string;
  readonly status?: number;
  readonly detail?: string;

  constructor(message: string, info: ApiRequestErrorInfo) {
    super(message);
    this.name = 'ApiRequestError';
    this.kind = info.kind;
    this.url = info.url;
    this.status = info.status;
    this.detail = info.detail;
  }
}

/** 本机服务不可达时的统一提示：告诉用户下一步做什么，而不是回显浏览器原始英文。 */
export const LOCAL_SERVICE_HINT =
  '无法连接本机服务（127.0.0.1）。请确认 CellFlow 窗口仍开着；'
  + '若刚重启过程序，请按 Ctrl+R 刷新本页。';

/** 与 services/async.ts 的 isAbortError 保持同一判定口径（避免两处语义漂移）。 */
export function isAbortLike(error: unknown): boolean {
  if (!error) return false;
  const name = (error as { name?: string }).name;
  if (name === 'AbortError') return true;
  const message = typeof error === 'string'
    ? error
    : (error as { message?: string }).message;
  return typeof message === 'string' && /abort(ed)?/i.test(message);
}

function looksLikeNetworkFailure(error: unknown): boolean {
  if (typeof TypeError !== 'undefined' && error instanceof TypeError) return true;
  const message = typeof error === 'string' ? error : (error as { message?: string })?.message;
  if (typeof message !== 'string') return false;
  return /failed to fetch|network ?error|load failed|network request failed/i.test(message);
}

/**
 * 把 fetch() 抛出的异常翻译成可判断的类型。
 * 返回 null 表示这是一次真正的中止，调用方应当原样 rethrow。
 */
export function classifyTransportError(error: unknown, url: string): ApiRequestError | null {
  if (isAbortLike(error)) return null;
  const detail = error instanceof Error
    ? `${error.name}: ${error.message}`
    : String(error);
  if (looksLikeNetworkFailure(error)) {
    return new ApiRequestError(LOCAL_SERVICE_HINT, { kind: 'transport', url, detail });
  }
  return new ApiRequestError(`请求失败：${detail}`, { kind: 'transport', url, detail });
}

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json() as { detail?: unknown };
    if (body && typeof body.detail === 'string' && body.detail) return body.detail;
  } catch {
    /* 非 JSON 错误体：退回状态行 */
  }
  return `${response.status} ${response.statusText}`;
}

/** 统一的 JSON 请求。fetchImpl 可注入，便于在 node 下直接对失败分类做回归测试。 */
export async function requestJson<T>(
  url: string,
  options?: RequestInit,
  fetchImpl: FetchLike = fetch,
): Promise<T> {
  let response: Response;
  try {
    response = await fetchImpl(url, options);
  } catch (error) {
    const classified = classifyTransportError(error, url);
    if (classified === null) throw error;             // 中止：原样交给调用方
    throw classified;
  }
  if (!response.ok) {
    throw new ApiRequestError(await readErrorDetail(response), {
      kind: 'http', url, status: response.status,
    });
  }
  try {
    return await response.json() as T;
  } catch (error) {
    throw new ApiRequestError('服务返回的不是合法 JSON，可能被中断或被代理改写。', {
      kind: 'parse', url,
      status: response.status,
      detail: error instanceof Error ? error.message : String(error),
    });
  }
}

/**
 * 一次请求失败该怎么处理：
 *   - 'missing'   任务确实不存在（服务重启后状态被清理）=> 允许清除本地记录；
 *   - 'transient' 服务暂时不可达/响应被中断 => 保留任务，稍后重试；
 *   - 'fatal'     业务错误（4xx/5xx、解析失败）=> 提示用户，不再盲重试。
 */
export type FailureClass = 'missing' | 'transient' | 'fatal';

export function classifyFailure(error: unknown): FailureClass {
  if (error instanceof ApiRequestError) {
    if (error.kind === 'http' && error.status === 404) return 'missing';
    if (error.kind === 'transport') return 'transient';
    if (error.kind === 'parse') return 'transient';
    return 'fatal';
  }
  if (looksLikeNetworkFailure(error)) return 'transient';
  return 'fatal';
}

export function isTransientFailure(error: unknown): boolean {
  return classifyFailure(error) === 'transient';
}

/** 任务已不存在（服务重启、状态被清理）——与暂时失联必须分开处理。 */
export function isMissingTaskError(error: unknown): boolean {
  return classifyFailure(error) === 'missing';
}
