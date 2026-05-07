const CACHE = 'hebpipe-v1';
const jobs = new Map(); // callId -> { pollUrl, deadline }

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', () => {});  // no clients.claim() — avoids interrupting mid-session fetches

self.addEventListener('message', event => {
  const { type, callId, pollUrl, deadline } = event.data || {};
  if (type === 'POLL_START' && callId && !jobs.has(callId)) {
    jobs.set(callId, { pollUrl, deadline });
    runPoll(callId);
  }
  if (type === 'POLL_CANCEL' && callId) {
    jobs.delete(callId);
  }
});

async function runPoll(callId) {
  const job = jobs.get(callId);
  if (!job) return;
  const { pollUrl, deadline } = job;

  while (jobs.has(callId) && Date.now() < deadline) {
    try {
      const resp = await fetch(pollUrl);
      if (resp.status === 200) {
        const data = await resp.json();
        jobs.delete(callId);
        const cache = await caches.open(CACHE);
        await cache.put('result:' + callId,
          new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } }));
        broadcast({ type: 'SW_DONE', callId, result: data });
        return;
      }
      if (resp.status !== 202) {
        jobs.delete(callId);
        broadcast({ type: 'SW_ERROR', callId });
        return;
      }
    } catch (_) {}
    await new Promise(r => setTimeout(r, 3000));
  }

  if (jobs.has(callId)) {
    jobs.delete(callId);
    broadcast({ type: 'SW_TIMEOUT', callId });
  }
}

async function broadcast(msg) {
  const clients = await self.clients.matchAll({ includeUncontrolled: true, type: 'window' });
  clients.forEach(c => c.postMessage(msg));
}
