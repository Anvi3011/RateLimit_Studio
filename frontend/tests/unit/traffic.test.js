import test from 'node:test';
import assert from 'node:assert/strict';
import { runTraffic, TRAFFIC_LIMITS } from '../../src/traffic.js';

// Transport doubles are confined to these unit tests. Browser tests use the
// actual local Flask/Redis/RabbitMQ/PostgreSQL deployment.
const response = (status, data) => ({ status, json: async () => data });
const session = () => response(200, { session_id: 'unit-test-session' });
const accepted = (id = 'test-request') => response(200, { request_id: id, decision: 'accepted' });

function clockedTransport(decision = () => accepted()) {
  let clock = 0;
  const calls = [];
  return { calls, now: () => clock,
    wait: async (ms) => { clock += ms; },
    fetchImpl: async (path, options) => {
      calls.push({ path, options, time: clock });
      return path === '/api/session' ? session() : decision();
    } };
}

test('target RPS paces every real decision submission, preserving identity/cookies', async () => {
  const transport = clockedTransport();
  const result = await runTraffic({ count: 100, rps: 50, clientId: 'client-A', endpoint: '/limited', ...transport });
  const decisions = transport.calls.filter((call) => call.path === '/api/requests/test');
  assert.equal(transport.calls[0].path, '/api/session');
  assert.equal(decisions.length, 100);
  assert.equal(result.accepted, 100);
  assert.equal(result.attempted, 100);
  for (let i = 1; i < decisions.length; i++) assert.ok(decisions[i].time - decisions[i - 1].time >= 20);
  for (const call of decisions) {
    assert.equal(call.options.credentials, 'same-origin');
    assert.equal(call.options.method, 'POST');
    assert.deepEqual(JSON.parse(call.options.body), { client_id: 'client-A', endpoint: '/limited' });
  }
});

test('different target speeds change submission intervals', async () => {
  for (const rps of [1, 10, 200]) {
    const transport = clockedTransport();
    await runTraffic({ count: 3, rps, ...transport });
    const decisions = transport.calls.slice(1);
    assert.equal(decisions[1].time - decisions[0].time, 1000 / rps);
  }
});

test('count bounds preserve the existing 500-request backend safety cap', async () => {
  for (const count of [0, -1, 501, 1000, 1.5, NaN]) {
    const transport = clockedTransport();
    await assert.rejects(runTraffic({ count, rps: 50, ...transport }), RangeError);
    assert.equal(transport.calls.length, 0);
  }
  for (const count of [1, 500]) {
    const transport = clockedTransport();
    assert.equal((await runTraffic({ count, rps: 200, ...transport })).attempted, count);
  }
});

test('invalid RPS values never send traffic', async () => {
  for (const rps of [0, -1, 201, 1.5, NaN]) {
    const transport = clockedTransport();
    await assert.rejects(runTraffic({ count: 1, rps, ...transport }), RangeError);
    assert.equal(transport.calls.length, 0);
  }
});

test('HTTP 429 is a genuine rejected decision, not a transport error or a retry', async () => {
  let count = 0;
  const transport = clockedTransport(() => ++count % 2 ? accepted()
    : response(429, { request_id: 'limited', decision: 'rejected' }));
  const result = await runTraffic({ count: 8, rps: 50, ...transport });
  assert.equal(result.accepted, 4);
  assert.equal(result.rejected, 4);
  assert.equal(result.errors, 0);
  assert.equal(result.stopReason, null);
  assert.equal(transport.calls.length, 9);
});

test('slow responses apply backpressure, without catch-up bursts', async () => {
  let inFlight = 0, peak = 0;
  const starts = [];
  const result = await runTraffic({ count: 8, rps: 200,
    limits: { ...TRAFFIC_LIMITS, maxInFlight: 2 },
    fetchImpl: async (path) => {
      if (path === '/api/session') return session();
      starts.push(performance.now());
      peak = Math.max(peak, ++inFlight);
      await new Promise((resolve) => setTimeout(resolve, 35));
      inFlight--;
      return accepted();
    } });
  assert.equal(peak, 2);
  assert.equal(result.attempted, 8);
  assert.ok(result.achievedRps < result.targetRps);
  assert.ok(starts[2] - starts[0] >= 30);
});

test('network failures stop new submissions and are not retried', async () => {
  const transport = clockedTransport(() => { throw new TypeError('Network unavailable'); });
  const result = await runTraffic({ count: 50, rps: 50, ...transport });
  assert.equal(result.stopReason, 'network_error');
  assert.ok(result.attempted < 50);
  assert.equal(result.unknown, result.errors);
  assert.equal(transport.calls.length, result.attempted + 1);
});

test('HTTP 503 stops submission and reports known backend errors honestly', async () => {
  const transport = clockedTransport(() => response(503, { request_id: 'error', decision: 'error' }));
  const result = await runTraffic({ count: 50, rps: 50, ...transport });
  assert.equal(result.stopReason, 'backend_error');
  assert.ok(result.errors > 0);
  assert.equal(result.unknown, 0);
  assert.ok(result.attempted < 50);
});

test('enqueue failures are surfaced instead of claiming guaranteed SQL logging', async () => {
  const transport = clockedTransport(() => response(200, {
    request_id: 'warning', decision: 'accepted', event_publish_error: 'outbox_unavailable',
  }));
  const result = await runTraffic({ count: 2, rps: 50, ...transport });
  assert.equal(result.accepted, 2);
  assert.equal(result.enqueueWarnings, 2);
});

test('failed session initialization never starts decision traffic', async () => {
  let calls = 0;
  const result = await runTraffic({ count: 5, rps: 50, fetchImpl: async () => {
    calls++;
    return response(503, {});
  } });
  assert.equal(calls, 1);
  assert.equal(result.attempted, 0);
  assert.equal(result.stopReason, 'session_unavailable');
});

function hangingTransport() {
  return async (path, options) => {
    if (path === '/api/session') return session();
    return new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
    });
  };
}

test('per-request timeout records an unknown outcome and stops without retrying', async () => {
  const result = await runTraffic({ count: 20, rps: 200, fetchImpl: hangingTransport(),
    limits: { ...TRAFFIC_LIMITS, requestTimeoutMs: 20 } });
  assert.equal(result.stopReason, 'request_timeout');
  assert.ok(result.unknown > 0);
  assert.ok(result.attempted <= 4);
});

test('cancellation aborts outstanding requests and preserves partial counts', async () => {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25);
  const result = await runTraffic({ count: 100, rps: 200, signal: controller.signal, fetchImpl: hangingTransport() });
  clearTimeout(timer);
  assert.equal(result.stopReason, 'cancelled');
  assert.ok(result.attempted <= 4);
  assert.equal(result.errors, result.attempted);
  assert.equal(result.unknown, result.attempted);
});

test('overall duration is bounded and cancellation before launch sends nothing', async () => {
  const result = await runTraffic({ count: 100, rps: 200, fetchImpl: hangingTransport(),
    limits: { ...TRAFFIC_LIMITS, maxDurationMs: 20 } });
  assert.equal(result.stopReason, 'duration_limit');
  const controller = new AbortController();
  controller.abort();
  let calls = 0;
  const cancelled = await runTraffic({ count: 1, rps: 1, signal: controller.signal,
    fetchImpl: async () => { calls++; return accepted(); } });
  assert.equal(calls, 0);
  assert.equal(cancelled.attempted, 0);
  assert.equal(cancelled.stopReason, 'cancelled');
});
