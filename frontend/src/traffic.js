// Browser pacing uses the existing real Flask decision endpoint. No automatic
// retries: a timed-out request may already have been processed and logged.
export const TRAFFIC_LIMITS = Object.freeze({
  maxCount: 500, maxInFlight: 4, requestTimeoutMs: 15000, maxDurationMs: 120000,
});

function delay(ms, signal) {
  return new Promise((resolve) => {
    const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve(); };
    const timer = setTimeout(finish, ms);
    signal.addEventListener('abort', finish, { once: true });
    if (signal.aborted) finish();
  });
}

export async function runTraffic({ count, rps, clientId = 'demo-client', endpoint = '/api/demo',
  signal, onProgress = () => {}, fetchImpl = globalThis.fetch,
  now = () => performance.now(), wait = delay, limits = TRAFFIC_LIMITS }) {
  if (!Number.isInteger(count) || count < 1 || count > TRAFFIC_LIMITS.maxCount)
    throw new RangeError('Use 1–500 requests per run; the existing backend safety cap is 500.');
  if (!Number.isInteger(rps) || rps < 1 || rps > 200)
    throw new RangeError('Target RPS must be a whole number from 1 to 200.');

  const controller = new AbortController();
  const cancel = () => controller.abort('cancelled');
  signal?.addEventListener('abort', cancel, { once: true });
  if (signal?.aborted) cancel();
  const started = now();
  const result = { requested: count, attempted: 0, accepted: 0, rejected: 0, errors: 0,
    unknown: 0, enqueueWarnings: 0, targetRps: rps, stopReason: null };
  const deadline = setTimeout(() => controller.abort('duration_limit'), limits.maxDurationMs);
  const snapshot = () => ({ ...result, elapsedSeconds: Math.max(0, now() - started) / 1000,
    achievedRps: result.attempted / Math.max(.001, (now() - started) / 1000) });
  const pending = new Set();

  async function request(path, options = {}) {
    const requestController = new AbortController();
    let timeout = false;
    const abort = () => requestController.abort();
    controller.signal.addEventListener('abort', abort, { once: true });
    if (controller.signal.aborted) abort();
    const timer = setTimeout(() => { timeout = true; abort(); }, limits.requestTimeoutMs);
    try {
      const response = await fetchImpl('/api' + path, { ...options,
        credentials: 'same-origin', signal: requestController.signal,
        headers: { 'Content-Type': 'application/json' } });
      const data = await response.json();
      return { status: response.status, data };
    } catch (error) {
      if (timeout) result.stopReason ||= 'request_timeout';
      throw error;
    } finally {
      clearTimeout(timer);
      controller.signal.removeEventListener('abort', abort);
    }
  }

  async function attempt() {
    try {
      const { status, data } = await request('/requests/test', { method: 'POST',
        body: JSON.stringify({ client_id: clientId, endpoint }) });
      if (data.request_id && status === 200 && data.decision === 'accepted') result.accepted++;
      else if (data.request_id && status === 429 && data.decision === 'rejected') result.rejected++;
      else {
        result.errors++;
        if (!data.request_id) result.unknown++;
        result.stopReason ||= 'backend_error';
      }
      if (data.event_publish_error) result.enqueueWarnings++;
    } catch (_) {
      result.errors++;
      result.unknown++;
      result.stopReason ||= 'network_error';
    } finally {
      onProgress(snapshot());
    }
  }

  try {
    // Establish the signed session cookie before any concurrent decision calls.
    if (!controller.signal.aborted) {
      try {
        const session = await request('/session');
        if (session.status !== 200 || !session.data.session_id) throw new Error('Session unavailable');
      } catch (_) {
        result.stopReason ||= 'session_unavailable';
      }
    }
    let nextAt = now();
    while (result.attempted < count && !controller.signal.aborted && !result.stopReason) {
      if (pending.size >= limits.maxInFlight) {
        await Promise.race(pending);
        continue;
      }
      await wait(Math.max(0, nextAt - now()), controller.signal);
      if (controller.signal.aborted || result.stopReason) break;
      // Space actual submissions. Do not burst to catch up after a slow response
      // or a background-tab timer delay.
      nextAt = now() + 1000 / rps;
      result.attempted++;
      const task = attempt();
      pending.add(task);
      task.finally(() => pending.delete(task));
    }
    await Promise.all(pending);
    if (controller.signal.aborted) result.stopReason = controller.signal.reason;
    return snapshot();
  } finally {
    clearTimeout(deadline);
    signal?.removeEventListener('abort', cancel);
  }
}
