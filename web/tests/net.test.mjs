import test from 'node:test';
import assert from 'node:assert/strict';
import {
  ApiRequestError, LOCAL_SERVICE_HINT, classifyTransportError, classifyFailure,
  isTransientFailure, isMissingTaskError, isAbortLike, requestJson,
} from '../.tmp-test/services/net.js';
import {
  createDownloadMonitor, shouldForgetTask, transientFailureMessage, lostMessage,
} from '../.tmp-test/services/download-monitor.js';

const flush = () => new Promise((resolve) => setTimeout(resolve, 0));
const classify = (error) => classifyFailure(error);

function makeScheduler() {
  let now = 0;
  let queue = [];
  return {
    schedule(callback, delay) {
      const handle = { callback, at: now + delay, dead: false };
      queue.push(handle);
      return handle;
    },
    cancel(handle) { if (handle) handle.dead = true; },
    async advance(ms) {
      now += ms;
      const due = queue.filter((h) => !h.dead && h.at <= now);
      queue = queue.filter((h) => !h.dead && h.at > now);
      for (const handle of due) handle.callback();
      await flush();
      await flush();
    },
    pending() { return queue.filter((h) => !h.dead).length; },
  };
}

// ---------------------------------------------------------------- 传输层分类

test('Failed to fetch 被分类为本机服务不可达，并给出可执行提示', async () => {
  const failure = new TypeError('Failed to fetch');
  const fetchImpl = async () => { throw failure; };
  await assert.rejects(
    () => requestJson('/api/downloads', { method: 'POST' }, fetchImpl),
    (error) => {
      assert.ok(error instanceof ApiRequestError, '应包装为 ApiRequestError');
      assert.equal(error.kind, 'transport');
      assert.equal(error.message, LOCAL_SERVICE_HINT);
      assert.match(error.detail, /TypeError/);
      assert.equal(classifyFailure(error), 'transient', '传输失败应按可重试处理');
      assert.equal(isTransientFailure(error), true);
      assert.equal(isMissingTaskError(error), false);
      return true;
    },
  );
});

test('主动取消（AbortError）必须原样抛出，不能被包装成服务不可达', async () => {
  const abort = new Error('The user aborted a request.');
  abort.name = 'AbortError';
  const fetchImpl = async () => { throw abort; };
  await assert.rejects(
    () => requestJson('/api/meta', {}, fetchImpl),
    (error) => {
      assert.equal(error, abort, '必须是同一个对象');
      return true;
    },
  );
  assert.equal(classifyTransportError(abort, '/api/meta'), null);
  assert.equal(isAbortLike(abort), true);
});

test('HTTP 500 保留状态码与后端 detail，不误判为断网', async () => {
  const response = {
    ok: false, status: 500, statusText: 'Internal Server Error',
    json: async () => ({ detail: '本地服务内部错误' }),
  };
  const fetchImpl = async () => response;
  await assert.rejects(
    () => requestJson('/api/downloads', {}, fetchImpl),
    (error) => {
      assert.equal(error.kind, 'http');
      assert.equal(error.status, 500);
      assert.equal(error.message, '本地服务内部错误');
      assert.equal(classifyFailure(error), 'fatal', '业务错误重试无意义');
      return true;
    },
  );
});

test('404 被识别为“任务已不存在”，与暂时失联区分开', async () => {
  const response = {
    ok: false, status: 404, statusText: 'Not Found',
    json: async () => ({ detail: '下载任务不存在' }),
  };
  await assert.rejects(
    () => requestJson('/api/downloads/abc', {}, async () => response),
    (error) => {
      assert.equal(isMissingTaskError(error), true);
      assert.equal(classifyFailure(error), 'missing');
      return true;
    },
  );
});

test('2xx 但不是 JSON 时给出可读错误而不是语法异常', async () => {
  const response = {
    ok: true, status: 200, statusText: 'OK',
    json: async () => { throw new SyntaxError('Unexpected token <'); },
  };
  await assert.rejects(
    () => requestJson('/api/meta', {}, async () => response),
    (error) => {
      assert.equal(error.kind, 'parse');
      assert.match(error.message, /不是合法 JSON/);
      assert.equal(classifyFailure(error), 'transient');
      return true;
    },
  );
});

// ---------------------------------------------------------------- 轮询状态机

const terminal = (status) => ['done', 'failed', 'cancelled'].includes(status.state);

test('单次失联不丢任务：上报 retry 后继续拿到 done', async () => {
  const scheduler = makeScheduler();
  const events = [];
  let calls = 0;
  const monitor = createDownloadMonitor({
    fetchStatus: async () => {
      calls += 1;
      if (calls === 1) throw new TypeError('Failed to fetch');
      return { id: 't1', state: calls === 2 ? 'running' : 'done' };
    },
    onEvent: (event) => events.push(event),
    isTerminal: terminal,
    classifyFailure: classify,
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  monitor.start('t1');
  await scheduler.advance(800);          // 第 1 次：失联
  await scheduler.advance(800);          // 第 2 次：running
  await scheduler.advance(800);          // 第 3 次：done
  assert.deepEqual(events.map((e) => e.type), ['retry', 'status', 'status']);
  assert.equal(events[0].failures, 1);
  assert.equal(events[2].status.state, 'done');
  assert.equal(scheduler.pending(), 0, '终止后不应继续轮询');
});

test('连续失联达到阈值停止轮询，但绝不丢弃任务记录', async () => {
  const scheduler = makeScheduler();
  const events = [];
  const monitor = createDownloadMonitor({
    fetchStatus: async () => { throw new TypeError('Failed to fetch'); },
    onEvent: (event) => events.push(event),
    isTerminal: terminal,
    classifyFailure: classify,
    maxTransientFailures: 3,
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  monitor.start('t2');
  for (let i = 0; i < 4; i += 1) await scheduler.advance(800);
  assert.deepEqual(events.map((e) => e.type), ['retry', 'retry', 'lost']);
  const lost = events.at(-1);
  assert.equal(lost.failures, 3);
  assert.equal(shouldForgetTask(lost), false, '失联时不能忘记任务');
  assert.equal(scheduler.pending(), 0);
  assert.match(transientFailureMessage(2), /仍在后台运行/);
  assert.match(lostMessage(3), /仍在后台运行/);
});

test('任务 404 才是“确已消失”，此时才允许 forget', async () => {
  const scheduler = makeScheduler();
  const events = [];
  const monitor = createDownloadMonitor({
    fetchStatus: async () => {
      throw new ApiRequestError('下载任务不存在', {
        kind: 'http', url: '/api/downloads/t3', status: 404,
      });
    },
    onEvent: (event) => events.push(event),
    isTerminal: terminal,
    classifyFailure: classify,
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  monitor.start('t3');
  await scheduler.advance(800);
  assert.deepEqual(events.map((e) => e.type), ['gone']);
  assert.equal(shouldForgetTask(events[0]), true);
  assert.equal(scheduler.pending(), 0);
});

test('业务错误（500）立即停止并上报 fatal，而不是无限重试', async () => {
  const scheduler = makeScheduler();
  const events = [];
  const monitor = createDownloadMonitor({
    fetchStatus: async () => {
      throw new ApiRequestError('本地服务内部错误', {
        kind: 'http', url: '/api/downloads/t4', status: 500,
      });
    },
    onEvent: (event) => events.push(event),
    isTerminal: terminal,
    classifyFailure: classify,
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  monitor.start('t4');
  await scheduler.advance(800);
  assert.deepEqual(events.map((e) => e.type), ['fatal']);
  assert.equal(scheduler.pending(), 0);
});

test('切换任务后旧轮询不再回写（避免张冠李戴）', async () => {
  const scheduler = makeScheduler();
  const events = [];
  const monitor = createDownloadMonitor({
    fetchStatus: async (id) => ({ id, state: 'running' }),
    onEvent: (event) => events.push(event),
    isTerminal: terminal,
    classifyFailure: classify,
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  monitor.start('a');
  monitor.start('b');
  await scheduler.advance(800);
  assert.equal(events.length, 1);
  assert.equal(events[0].status.id, 'b');
  assert.equal(scheduler.pending(), 1);
  monitor.stop();
});

test('停止后不再发出请求（组件卸载即取消）', async () => {
  const scheduler = makeScheduler();
  const events = [];
  let calls = 0;
  const monitor = createDownloadMonitor({
    fetchStatus: async () => { calls += 1; return { id: 'x', state: 'running' }; },
    onEvent: (event) => events.push(event),
    isTerminal: terminal,
    classifyFailure: classify,
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  monitor.start('x');
  monitor.stop();
  await scheduler.advance(5000);
  assert.equal(calls, 0);
  assert.equal(events.length, 0);
});
