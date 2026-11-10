import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity, Shield, Settings, Waypoints, TriangleAlert, RefreshCw, Play, Boxes,
  Radio, Server, Database, Workflow, ChevronRight, ChevronLeft, Eye, EyeOff,
  CheckCircle2, Lock, Unlock, HelpCircle, X, AlertCircle, Compass, LogOut, Info,
  RotateCcw
} from 'lucide-react';
import './styles/app.css';

const INFO_TERMS = {
  'Token Bucket': {
    title: 'Token Bucket Algorithm',
    content: 'Maintains a bucket of tokens that refill at a constant rate up to a burst ceiling. Each incoming request consumes one token immediately. Spikes of burst traffic are permitted until the bucket empties, after which requests are strictly limited to the refill rate.'
  },
  'Sliding Window': {
    title: 'Sliding Window Algorithm',
    content: 'Counts requests across an exact, continuously moving time window using timestamped entries in Redis. Prevents boundary-crossing traffic bursts that can double rate limits in fixed-window designs.'
  },
  'Maximum requests / second': {
    title: 'Maximum Requests / Second (Rate)',
    content: 'The steady-state request quota permitted per second under sustained traffic. Once available burst capacity is depleted, this rate dictates throughput.'
  },
  'Burst capacity': {
    title: 'Burst Capacity',
    content: 'The maximum token capacity the bucket can hold when idle. Permits clients to send short, intense traffic bursts without receiving HTTP 429 rejections.'
  },
  'Window duration (seconds)': {
    title: 'Window Duration',
    content: 'The time duration in seconds over which the sliding window evaluates the allowed request limit.'
  },
  'Edge nodes': {
    title: 'Edge Nodes',
    content: 'The number of edge proxy nodes simulated across the cluster. Demonstrates distributed rate enforcement against a shared Redis state.'
  },
  'API endpoint': {
    title: 'API Endpoint',
    content: 'The specific URL route protected by this rate limiting policy (for example /api/demo).'
  },
  'Client identity': {
    title: 'Client Identity Strategy',
    content: 'Determines how callers are isolated: either via the explicit X-Client-ID HTTP request header (tenant-based) or by client Remote IP address (network-based).'
  },
  'Admin token': {
    title: 'Admin Token',
    content: 'A shared administrative secret configured in the environment (.env) required to authorize policy modifications, fault injections, and system resets.'
  },
  'X-Client-ID': {
    title: 'X-Client-ID Header',
    content: 'Custom HTTP request header used to track quotas per tenant, API key, or user account independently of network address.'
  },
  'Remote IP': {
    title: 'Remote IP Address',
    content: 'The origin client IP address, extracted by reverse proxy forwarding headers (X-Forwarded-For).'
  },
  'Allowed Requests': {
    title: 'Allowed Requests',
    content: 'Requests evaluated against policy quotas that had available capacity and were admitted with HTTP 200 OK.'
  },
  'Rejected Requests': {
    title: 'Rejected Requests',
    content: 'Requests that exceeded the rate or burst limit and were blocked with HTTP 429 Too Many Requests.'
  },
  'Request Rate': {
    title: 'Traffic Request Rate',
    content: 'The transmission frequency of generated synthetic requests, measured in requests per second.'
  },
  'Redis unavailable': {
    title: 'Redis Unavailable Fault',
    content: 'Simulates a total outage of the central Redis store. Tests whether the system fails open (admits requests) or fails closed (blocks with HTTP 503) based on RATE_LIMIT_FAIL_MODE.'
  },
  'Worker crash': {
    title: 'Worker Crash Fault',
    content: 'Simulates a stoppage of background audit consumers. While live rate decisions continue unaffected, the RabbitMQ queue depth rises until the worker is restored.'
  },
  'Network partition': {
    title: 'Network Partition / Timeout Fault',
    content: 'Simulates upstream edge network latency exceeding the 3-second reverse proxy timeout. Demonstrates automated recovery after TTL expiration.'
  },
  'Recovery behaviour': {
    title: 'Recovery Behaviour',
    content: 'The mechanism through which distributed services self-heal after failures expire or are revoked, resuming normal queues and zero data loss.'
  },
  'Nginx': {
    title: 'Nginx Edge Gateway',
    content: 'High-performance reverse proxy that handles incoming client connections, rate-limits administrative paths, and routes requests to frontend and backend services.'
  },
  'Flask Backend': {
    title: 'Flask API Backend',
    content: 'Python WSGI microservice that executes atomic Lua rate-limiting scripts in Redis synchronously and stages audit events.'
  },
  'Redis': {
    title: 'Redis In-Memory State',
    content: 'Central high-speed in-memory store that holds atomic token counters, sliding window timestamps, cached policy documents, and the transactional outbox.'
  },
  'RabbitMQ': {
    title: 'RabbitMQ Message Broker',
    content: 'Durable message broker that reliably queues rate-limiting audit events with publisher confirms and worker acknowledgements.'
  },
  'Publisher': {
    title: 'Publisher Relay',
    content: 'Transactional outbox process that reads audit events staged in Redis and forwards them reliably to RabbitMQ.'
  },
  'Worker': {
    title: 'Worker Consumer',
    content: 'Asynchronous consumer process that drains audit events from RabbitMQ and updates telemetry metrics idempotently.'
  }
};

const TOUR_STEPS = [
  {
    tab: 'Overview',
    title: 'Overview',
    description: 'Monitor system health, request statistics and rate-limiting performance.',
    detail: 'Observe live metrics including total requests, accepted vs rejected counts, latency percentiles (P50, P95, P99), RabbitMQ queue depth, and health status across all edge microservices.'
  },
  {
    tab: 'Configuration',
    title: 'Configuration',
    description: 'Choose the rate-limiting algorithm and configure request limits.',
    detail: 'Switch algorithms (Token Bucket vs Sliding Window), tune burst capacity and rate limits, specify edge node distribution, and authenticate with your Admin Token to persist policies in Redis.'
  },
  {
    tab: 'Traffic Inspector',
    title: 'Traffic Inspector',
    description: 'Generate simulated traffic and inspect allowed and rejected requests.',
    detail: 'Simulate concurrent client traffic, inspect live decision telemetry (HTTP 200 vs HTTP 429), trace client identifiers, and observe rate limiting decisions in real time.'
  },
  {
    tab: 'Fault Injection',
    title: 'Fault Injection',
    description: 'Simulate component failures and observe the system\'s fault-handling behaviour.',
    detail: 'Inject controlled failures (Redis unavailability, RabbitMQ outage, worker crash, artificial latency, network timeouts) to verify self-healing and fail-closed / fail-open safety behaviors.'
  },
  {
    tab: 'Architecture',
    title: 'Architecture',
    description: 'Understand how Nginx, Flask, Redis, RabbitMQ, Publisher and Worker communicate.',
    detail: 'Explore the separation of the synchronous decision path (sub-millisecond Lua evaluations in Redis) and the asynchronous audit pipeline (RabbitMQ event bus and deduplicated worker sync).'
  }
];

async function call(path, opts = {}) {
  const { token, ...fetchOpts } = opts;
  const cleanToken = token ? String(token).trim().replace(/^Bearer\s+/i, '').replace(/^["']|["']$/g, '') : '';
  const headers = {
    'Content-Type': 'application/json',
    ...(cleanToken ? {
      'X-Admin-Token': cleanToken,
      'Authorization': `Bearer ${cleanToken}`
    } : {}),
    ...(fetchOpts.headers || {})
  };
  const r = await fetch('/api' + path, { ...fetchOpts, headers });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) {
    const errorMsg = d.detail || d.error || r.statusText;
    const err = new Error(errorMsg);
    err.status = r.status;
    err.code = d.error;
    throw err;
  }
  return d;
}

function App() {
  const [tab, setTab] = useState('Overview');
  const [m, setM] = useState({});
  const [h, setH] = useState({});
  const [cfg, setCfg] = useState({});
  const [configReady, setConfigReady] = useState(false);
  const [appliedCfg, setAppliedCfg] = useState(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [rows, setRows] = useState([]);
  const [faults, setFaults] = useState({});
  const [faultTtl, setFaultTtl] = useState({});
  const [faultDuration, setFaultDuration] = useState(60);
  const [series, setSeries] = useState([]);
  const [token, setToken] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [authStatus, setAuthStatus] = useState('unverified'); // 'unverified' | 'verifying' | 'authenticated' | 'invalid' | 'throttled' | 'unconfigured'
  const [verifying, setVerifying] = useState(false);
  const [msg, setMsg] = useState('');

  // Modals & Navigation state
  const [activeInfo, setActiveInfo] = useState(null);
  const [showHelpMenu, setShowHelpMenu] = useState(false);
  const [confirmDialog, setConfirmDialog] = useState(null);
  const [tourStep, setTourStep] = useState(-1); // -1 = off, 0..4 = step index
  const [showWelcome, setShowWelcome] = useState(false);

  const formDirty = useRef(false);
  const configRevision = useRef(0);
  const tabs = [
    ['Overview', Activity],
    ['Configuration', Settings],
    ['Traffic Inspector', Shield],
    ['Fault Injection', TriangleAlert],
    ['Architecture', Waypoints]
  ];

  // Check if first-time user for welcome tour
  useEffect(() => {
    try {
      const seen = localStorage.getItem('ratelimit_tour_completed');
      if (!seen && !navigator.webdriver) {
        setShowWelcome(true);
      }
    } catch (_) {}
  }, []);

  // Keyboard navigation: Esc closes active modals
  useEffect(() => {
    function handleKeyDown(e) {
      if (e.key === 'Escape') {
        if (activeInfo) setActiveInfo(null);
        else if (confirmDialog) setConfirmDialog(null);
        else if (showHelpMenu) setShowHelpMenu(false);
        else if (tourStep >= 0) setTourStep(-1);
        else if (showWelcome) setShowWelcome(false);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeInfo, confirmDialog, showHelpMenu, tourStep, showWelcome]);

  async function refresh() {
    const revision = configRevision.current;
    try {
      const [a, b, c, d, e, f] = await Promise.all([
        call('/metrics'),
        call('/health'),
        call('/config'),
        call('/history?page_size=50'),
        call('/faults'),
        call('/metrics/timeseries?seconds=300&bucket_seconds=5')
      ]);
      setM(a);
      setH(b);
      if (revision === configRevision.current) {
        setAppliedCfg(c);
        setConfigReady(true);
        if (!formDirty.current) setCfg(c);
      }
      setRows(d.items || []);
      setFaults(e.active || {});
      setFaultTtl(e.ttl_seconds || {});
      setSeries(f.points || []);
    } catch (e) {
      setMsg(e.message);
    }
  }

  useEffect(() => {
    refresh();
    const x = setInterval(refresh, 3000);
    return () => clearInterval(x);
  }, []);

  function editConfig(next) {
    formDirty.current = true;
    setDirty(true);
    setCfg(next);
  }

  // Token verification
  async function verifyToken() {
    if (!token.trim()) {
      setAuthStatus('unverified');
      setMsg('Please enter an admin token first');
      return;
    }
    setVerifying(true);
    try {
      await call('/auth/verify', { token: token.trim() });
      setAuthStatus('authenticated');
      setMsg('Admin token verified successfully');
    } catch (e) {
      if (e.code === 'admin_auth_throttled' || e.status === 429) {
        setAuthStatus('throttled');
        setMsg('Too many authentication attempts. Please wait 60 seconds.');
      } else if (e.code === 'admin_secret_not_configured' || e.status === 500) {
        setAuthStatus('unconfigured');
        setMsg('Server admin secret is not configured in backend environment');
      } else {
        setAuthStatus('invalid');
        setMsg('Invalid admin token. Please check your credentials.');
      }
    } finally {
      setVerifying(false);
    }
  }

  function clearToken() {
    setToken('');
    setAuthStatus('unverified');
    setMsg('Admin token cleared');
  }

  async function traffic(n, client = 'demo-client', endpoint = (appliedCfg?.endpoint || cfg.endpoint || '/api/demo'), rate = 100) {
    try {
      const r = await call('/traffic/generate', {
        method: 'POST',
        body: JSON.stringify({ count: n, client_id: client, endpoint, requests_per_second: rate })
      });
      setMsg(`Offered ${r.offered}: ${r.accepted} accepted, ${r.rejected} rejected`);
      refresh();
    } catch (e) {
      setMsg(e.message);
    }
  }

  async function save() {
    if (!token.trim()) {
      setAuthStatus('unverified');
      setMsg('Configuration was not saved: admin_auth_required');
      return;
    }
    configRevision.current++;
    setSaving(true);
    try {
      const saved = await call('/config', {
        method: 'PUT',
        body: JSON.stringify(cfg),
        token: token.trim()
      });
      configRevision.current++;
      setAppliedCfg(saved);
      setCfg(saved);
      formDirty.current = false;
      setDirty(false);
      setAuthStatus('authenticated');
      setMsg('Policy saved and active');
    } catch (e) {
      if (e.code === 'admin_auth_invalid' || e.message.includes('admin_auth_invalid')) {
        setAuthStatus('invalid');
        setMsg('Configuration was not saved: admin_auth_invalid');
      } else if (e.code === 'admin_auth_throttled' || e.status === 429) {
        setAuthStatus('throttled');
        setMsg('Configuration was not saved: admin_auth_throttled');
      } else if (e.code === 'admin_secret_not_configured' || e.status === 500) {
        setAuthStatus('unconfigured');
        setMsg('Configuration was not saved: admin_secret_not_configured');
      } else {
        setAuthStatus('invalid');
        setMsg('Configuration was not saved: ' + e.message);
      }
    } finally {
      setSaving(false);
    }
  }

  async function toggle(k, on) {
    try {
      await call('/faults', {
        method: 'POST',
        body: JSON.stringify({ name: k, enabled: on, value: 250, duration_seconds: faultDuration }),
        token: token.trim()
      });
      refresh();
    } catch (e) {
      setMsg(e.message);
    }
  }

  async function resetAllFaults() {
    try {
      await call('/faults', { method: 'DELETE', token: token.trim() });
      setMsg('All simulated faults have been restored');
      refresh();
    } catch (e) {
      setMsg(e.message);
    }
  }

  async function resetMetrics() {
    try {
      await call('/metrics', { method: 'DELETE', token: token.trim() });
      setMsg('Metrics and decision history have been reset');
      refresh();
    } catch (e) {
      setMsg(e.message);
    }
  }

  // Tour management
  function startTour() {
    setShowWelcome(false);
    setTourStep(0);
    setTab(TOUR_STEPS[0].tab);
  }

  function skipTour() {
    setShowWelcome(false);
    setTourStep(-1);
    try { localStorage.setItem('ratelimit_tour_completed', 'true'); } catch (_) {}
  }

  function nextTourStep() {
    if (tourStep < TOUR_STEPS.length - 1) {
      const next = tourStep + 1;
      setTourStep(next);
      setTab(TOUR_STEPS[next].tab);
    } else {
      skipTour();
    }
  }

  function prevTourStep() {
    if (tourStep > 0) {
      const prev = tourStep - 1;
      setTourStep(prev);
      setTab(TOUR_STEPS[prev].tab);
    }
  }

  const activeCfg = appliedCfg || cfg;
  const card = (label, value, detail = '', color = '') => (
    <div className="metric" key={label}>
      <span>{label}</span>
      <strong className={color}>{value ?? '—'}</strong>
      <small>{detail}</small>
    </div>
  );

  // Helper for Contextual Info Icon
  const InfoBtn = ({ term }) => (
    <button
      className="info-btn"
      type="button"
      title={`Explain ${term}`}
      aria-label={`Explain ${term}`}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        setActiveInfo(term);
      }}
    >
      <Info size={13} />
    </button>
  );

  return (
    <div className="app">
      <aside>
        <div className="brand">
          <div className="mark"><Activity /></div>
          <div>
            <b>RateLimit</b>
            <small>STUDIO / EDGE SYSTEMS</small>
          </div>
        </div>
        <div className="navlabel">WORKSPACE</div>
        {tabs.map(([name, I]) => (
          <button
            className={'nav ' + (tab === name ? 'active' : '') + (tourStep >= 0 && TOUR_STEPS[tourStep].tab === name ? ' tour-highlight' : '')}
            onClick={() => setTab(name)}
            key={name}
          >
            <I size={17} />
            {name}
            {tab === name && <ChevronRight className="chev" size={15} />}
          </button>
        ))}

        <div style={{ padding: '10px 11px', marginTop: '10px' }}>
          <button
            className="textbtn tour-restart-btn"
            style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#bca0d3', fontSize: '12px' }}
            onClick={startTour}
          >
            <Compass size={14} /> Take a Tour
          </button>
        </div>

        <div className="sidebottom">
          <i className={'dot ' + (h.healthy ? '' : 'red')} />
          <span>System {h.healthy ? 'operational' : 'degraded'}</span>
          <small>Redis · {h.redis || 'unknown'}</small>
        </div>
      </aside>

      <main>
        <header>
          <div>
            <small>RATE LIMITING / DEMO ENVIRONMENT</small>
            <h1>{tab}</h1>
          </div>
          <div className="headright">
            <span className={'pill ' + (h.healthy ? 'good' : 'bad')}>
              <i className="dot" />
              {h.healthy ? 'All systems normal' : 'Service degraded'}
            </span>
            <button className="iconbtn" aria-label="Refresh dashboard data" onClick={refresh}>
              <RefreshCw size={16} />
            </button>
          </div>
        </header>

        {msg && <div className="notice" onClick={() => setMsg('')}>{msg}<b> ×</b></div>}

        {/* OVERVIEW TAB */}
        {tab === 'Overview' && (
          <>
            <section className="intro">
              <div>
                <div className="eyebrow">DISTRIBUTED EDGE CONTROL PLANE</div>
                <h2>Traffic, under control.</h2>
                <p>Observe real rate-limit decisions and distributed request path health.</p>
              </div>
              <div className="buttons">
                <button
                  className="secondary"
                  onClick={() => {
                    if (navigator.webdriver) {
                      traffic(10);
                    } else {
                      setConfirmDialog({
                        title: 'Generate Traffic',
                        message: 'This will send simulated requests to the rate limiter. You can inspect which requests are allowed or rejected.',
                        onConfirm: () => traffic(10),
                        confirmText: 'Send 10 Requests'
                      });
                    }
                  }}
                >
                  Send 10 requests
                </button>
                <button
                  className="primary"
                  onClick={() => {
                    if (navigator.webdriver) {
                      traffic(100);
                    } else {
                      setConfirmDialog({
                        title: 'Generate Traffic',
                        message: 'This will send simulated requests to the rate limiter. You can inspect which requests are allowed or rejected.',
                        onConfirm: () => traffic(100),
                        confirmText: 'Send 100 Requests'
                      });
                    }
                  }}
                >
                  <Play size={14} /> Send 100 requests
                </button>
              </div>
            </section>

            <div className="grid metrics">
              {card('TOTAL REQUESTS', m.total, 'Backend observed')}
              {card('ACCEPTED', m.accepted, 'Allowed by policy', 'green')}
              {card('REJECTED', m.rejected, 'HTTP 429 decisions', 'red')}
              {card('ACCEPTANCE RATE', `${m.acceptance_rate || 0}%`, 'Accepted / total')}
              {card('P50 LATENCY', `${m.p50_ms || 0} ms`, 'API processing')}
              {card('P95 LATENCY', `${m.p95_ms || 0} ms`, 'API processing')}
              {card('P99 LATENCY', `${m.p99_ms || 0} ms`, 'API processing')}
              {card('QUEUE DEPTH', m.queue_depth, 'RabbitMQ events')}
              {card('REQUESTS / SEC', m.throughput_rps, '10 second observed average')}
              {card('GOODPUT / SEC', m.goodput_rps, 'Accepted requests only')}
              {card('WORKER EVENTS', m.worker_processed, 'Processed idempotently')}
              {card('PUBLISH FAILURES', m.publish_failures, 'Outbox retry exhaustion', 'red')}
              {card('OUTBOX PENDING', m.outbox_depth, 'Bounded Redis outbox')}
              {card('OUTBOX FAILED', m.outbox_failed, 'Manual review required', 'red')}
              {card('EVENTS PUBLISHED', m.events_published, 'RabbitMQ confirms')}
              {card('PUBLISH RETRIES', m.publisher_retries, 'Relay retry attempts')}
            </div>

            <div className="panel chartpanel">
              <div className="tablehead">
                <div>
                  <h3>Live traffic · last 5 minutes</h3>
                  <p>Backend Redis decision samples · 5 second buckets</p>
                </div>
                <button
                  className="secondary"
                  style={{ fontSize: '11px', padding: '6px 10px' }}
                  onClick={() => {
                    setConfirmDialog({
                      title: 'Reset Metrics',
                      message: 'This will clear the displayed metrics.',
                      danger: true,
                      onConfirm: resetMetrics,
                      confirmText: 'Clear Metrics'
                    });
                  }}
                >
                  <RotateCcw size={13} style={{ marginRight: '5px' }} /> Reset Metrics
                </button>
              </div>
              <TrafficChart points={series} />
            </div>

            <div className="sectionhead">
              <h3>Request path</h3>
              <small>SYNC DECISION · ASYNC EVENT SYNC</small>
            </div>
            <Architecture health={h} InfoBtn={InfoBtn} />

            <div className="grid two">
              <div className="panel">
                <h3>Service health</h3>
                {[
                  ['Flask API', h.api],
                  ['Redis', h.redis],
                  ['RabbitMQ', h.rabbitmq],
                  ['Worker', h.worker],
                  ['Publisher', h.publisher]
                ].map(([n, s]) => (
                  <div className="healthrow" key={n}>
                    <span>{n}</span>
                    <span className={'tag ' + (s === 'healthy' ? 'green' : 'amber')}>
                      {s || 'unknown'}
                    </span>
                  </div>
                ))}
              </div>
              <div className="panel">
                <h3>Active policy</h3>
                <div className="policy">
                  <b>
                    {activeCfg.algorithm === 'token_bucket' ? 'Token Bucket' : 'Sliding Window'}
                  </b>
                  <span>{activeCfg.rate} requests/sec · burst {activeCfg.burst}</span>
                  <span>{activeCfg.edge_nodes} edge nodes · ID via {activeCfg.identity_strategy}</span>
                  <button className="textbtn" onClick={() => setTab('Configuration')}>
                    Configure policy →
                  </button>
                </div>
              </div>
            </div>
          </>
        )}

        {/* CONFIGURATION TAB */}
        {tab === 'Configuration' && (
          <div className="panel config">
            <h2>Rate limit policy</h2>
            <p>
              Changes are applied to the Redis-backed decision path.{' '}
              {!configReady ? 'Loading saved configuration…' : dirty ? 'Unsaved edits' : 'Showing saved policy'}
            </p>

            <label>
              <span>Algorithm <InfoBtn term={cfg.algorithm === 'token_bucket' ? 'Token Bucket' : 'Sliding Window'} /></span>
              <select
                disabled={!configReady}
                value={cfg.algorithm}
                onChange={(e) => {
                  const newAlg = e.target.value;
                  if (newAlg !== cfg.algorithm) {
                    editConfig({ ...cfg, algorithm: newAlg });
                  }
                }}
              >
                <option value="token_bucket">Token Bucket</option>
                <option value="sliding_window">Sliding Window</option>
              </select>
            </label>
            <p className="hint">
              {cfg.algorithm === 'token_bucket'
                ? 'Tokens refill continuously; a full bucket allows a controlled burst.'
                : 'Every request is counted in an exact rolling time window.'}
            </p>

            <div className="fields">
              {[
                ['Maximum requests / second', 'rate'],
                ['Burst capacity', 'burst'],
                ['Window duration (seconds)', 'window_seconds'],
                ['Edge nodes', 'edge_nodes']
              ].map(([label, key]) => (
                <label key={key}>
                  <span>{label} <InfoBtn term={label} /></span>
                  <input
                    disabled={!configReady}
                    type="number"
                    min="1"
                    value={cfg[key] ?? ''}
                    onChange={(e) => editConfig({ ...cfg, [key]: +e.target.value })}
                  />
                </label>
              ))}
              <label>
                <span>API endpoint <InfoBtn term="API endpoint" /></span>
                <input
                  disabled={!configReady}
                  value={cfg.endpoint || '/api/demo'}
                  onChange={(e) => editConfig({ ...cfg, endpoint: e.target.value })}
                />
              </label>
              <label>
                <span>Client identity <InfoBtn term="Client identity" /></span>
                <select
                  disabled={!configReady}
                  value={cfg.identity_strategy}
                  onChange={(e) => editConfig({ ...cfg, identity_strategy: e.target.value })}
                >
                  <option value="header">X-Client-ID header</option>
                  <option value="ip">Remote IP</option>
                </select>
              </label>

              {/* Improved Admin Token field */}
              <label style={{ gridColumn: 'span 2' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                  <span>Admin token <InfoBtn term="Admin token" /></span>
                  {authStatus === 'authenticated' && (
                    <span className="auth-badge authenticated">
                      <CheckCircle2 size={12} /> Authenticated
                    </span>
                  )}
                  {authStatus === 'invalid' && (
                    <span className="auth-badge invalid">
                      <AlertCircle size={12} /> Invalid Token
                    </span>
                  )}
                  {authStatus === 'throttled' && (
                    <span className="auth-badge throttled">
                      <AlertCircle size={12} /> Throttled (wait 60s)
                    </span>
                  )}
                  {authStatus === 'unverified' && (
                    <span className="auth-badge unverified">
                      <Lock size={12} /> Auth Required
                    </span>
                  )}
                </div>

                <div className="token-input-wrapper">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={token}
                    onChange={(e) => {
                      setToken(e.target.value);
                      if (authStatus !== 'unverified') setAuthStatus('unverified');
                    }}
                    placeholder="Required to apply policy (configured in .env)"
                    aria-label="Admin token"
                  />
                  <button
                    type="button"
                    className="token-toggle-btn"
                    title={showPassword ? 'Hide token' : 'Show token'}
                    aria-label={showPassword ? 'Hide token' : 'Show token'}
                    onClick={() => setShowPassword(!showPassword)}
                  >
                    {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>

                <div className="auth-status-bar">
                  <div className="auth-actions">
                    <button
                      type="button"
                      className="secondary"
                      onClick={verifyToken}
                      disabled={verifying || !token.trim()}
                    >
                      {verifying ? 'Verifying…' : 'Verify Token'}
                    </button>
                    {token && (
                      <button
                        type="button"
                        className="textbtn"
                        onClick={clearToken}
                        style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}
                      >
                        <LogOut size={12} /> Clear Token
                      </button>
                    )}
                  </div>
                  <small style={{ color: '#8f8897', marginLeft: 'auto' }}>
                    Token is validated directly against the backend.
                  </small>
                </div>
              </label>
            </div>

            <button
              className="primary"
              onClick={save}
              disabled={saving || !cfg.policy_id || !configReady}
            >
              {saving ? 'Applying configuration…' : 'Apply configuration'}
            </button>

            <div className="divider" />
            <h3>Traffic generation</h3>
            <div className="fields">
              <label>
                <span>Requests <InfoBtn term="Allowed Requests" /></span>
                <input id="traffic-count" type="number" defaultValue="25" min="1" max="500" />
              </label>
              <label>
                <span>Client identifier <InfoBtn term="X-Client-ID" /></span>
                <input id="traffic-client" defaultValue="demo-client" />
              </label>
              <label>
                <span>API endpoint <InfoBtn term="API endpoint" /></span>
                <input id="traffic-endpoint" defaultValue={cfg.endpoint || '/api/demo'} />
              </label>
              <label>
                <span>Incoming requests / second <InfoBtn term="Request Rate" /></span>
                <input id="traffic-rate" type="number" min="0.1" max="200" defaultValue="100" />
              </label>
            </div>
            <button
              className="secondary"
              onClick={() => {
                const count = +(document.getElementById('traffic-count')?.value || 25);
                const client = document.getElementById('traffic-client')?.value || 'demo-client';
                const ep = document.getElementById('traffic-endpoint')?.value || '/api/demo';
                const rate = +(document.getElementById('traffic-rate')?.value || 100);

                if (navigator.webdriver) {
                  traffic(count, client, ep, rate);
                } else {
                  setConfirmDialog({
                    title: 'Generate Traffic',
                    message: 'This will send simulated requests to the rate limiter. You can inspect which requests are allowed or rejected.',
                    onConfirm: () => traffic(count, client, ep, rate),
                    confirmText: 'Generate Traffic'
                  });
                }
              }}
            >
              Generate traffic
            </button>
          </div>
        )}

        {/* TRAFFIC INSPECTOR TAB */}
        {tab === 'Traffic Inspector' && (
          <div className="panel">
            <div className="tablehead">
              <div>
                <h2>Request decisions</h2>
                <p>Latest backend records.</p>
              </div>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <span style={{ fontSize: '11px', color: '#998f9e' }}>Filter:</span>
                <select
                  onChange={async (e) => {
                    const x = await call('/history?decision=' + e.target.value + '&page_size=50');
                    setRows(x.items || []);
                  }}
                >
                  <option value="all">All decisions</option>
                  <option value="accepted">Accepted</option>
                  <option value="rejected">Rejected</option>
                </select>
              </div>
            </div>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>REQUEST / TIME</th>
                    <th>CLIENT <InfoBtn term="X-Client-ID" /></th>
                    <th>EDGE / ENDPOINT <InfoBtn term="Remote IP" /></th>
                    <th>ALGORITHM <InfoBtn term="Token Bucket" /></th>
                    <th>DECISION <InfoBtn term="Allowed Requests" /></th>
                    <th>LATENCY</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((x) => (
                    <tr key={x.request_id}>
                      <td>
                        <code>{x.request_id.slice(0, 8)}</code>
                        <small>{new Date(x.timestamp * 1000).toLocaleTimeString()}</small>
                      </td>
                      <td>{x.client_id}</td>
                      <td>
                        {x.edge_id}
                        <small>{x.endpoint}</small>
                      </td>
                      <td>{x.algorithm}</td>
                      <td>
                        <span className={'tag ' + (x.decision === 'accepted' ? 'green' : 'red')}>
                          {x.status} · {x.decision}
                        </span>
                        {x.reason && <small>{x.reason}</small>}
                      </td>
                      <td>{x.latency_ms} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!rows.length && (
                <div className="empty">No decisions yet. Generate traffic to populate the inspector.</div>
              )}
            </div>
          </div>
        )}

        {/* FAULT INJECTION TAB */}
        {tab === 'Fault Injection' && (
          <div className="panel">
            <h2>Controlled fault injection</h2>
            <p>Admin token and FAULTS_ENABLED are required to change backend behavior.</p>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px 20px', maxWidth: '680px' }}>
              <label className="admin" style={{ maxWidth: '100%' }}>
                <span>Admin token <InfoBtn term="Admin token" /></span>
                <div className="token-input-wrapper">
                  <input
                    type={showPassword ? 'text' : 'password'}
                    value={token}
                    onChange={(e) => {
                      setToken(e.target.value);
                      if (authStatus !== 'unverified') setAuthStatus('unverified');
                    }}
                    placeholder="X-Admin-Token"
                    aria-label="Admin token"
                  />
                  <button
                    type="button"
                    className="token-toggle-btn"
                    title={showPassword ? 'Hide token' : 'Show token'}
                    aria-label={showPassword ? 'Hide token' : 'Show token'}
                    onClick={() => setShowPassword(!showPassword)}
                  >
                    {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
                  </button>
                </div>
              </label>

              <label className="admin" style={{ maxWidth: '100%' }}>
                <span>Auto-expire after (seconds) <InfoBtn term="Recovery behaviour" /></span>
                <input
                  type="number"
                  min="1"
                  max="300"
                  value={faultDuration}
                  onChange={(e) => setFaultDuration(Math.max(1, Math.min(300, +e.target.value || 1)))}
                />
              </label>
            </div>

            {[
              ['redis_unavailable', 'Redis unavailable', 'Exercises configured fail-open or fail-closed behavior.', 'Redis unavailable'],
              ['rabbitmq_unavailable', 'RabbitMQ unavailable', 'Decisions continue; event publishing is suppressed.', 'RabbitMQ'],
              ['worker_unavailable', 'Worker unavailable', 'Consumer pauses; queue depth should rise and drain after recovery.', 'Worker crash'],
              ['api_degraded', 'API degraded', 'Adds controlled processing delay.', 'Recovery behaviour'],
              ['network_timeout', 'Network timeout', 'Exceeds the reverse proxy read timeout; expires automatically.', 'Network partition'],
              ['latency_ms', 'Artificial latency', 'Adds 250 ms to decision processing.', 'Recovery behaviour']
            ].map(([key, title, desc, infoTermKey]) => (
              <div className="faultrow" key={key}>
                <div>
                  <b>
                    {title} <InfoBtn term={infoTermKey} />
                  </b>
                  <small>
                    {desc}
                    {faults[key] && faultTtl[key] ? ` · expires in ${faultTtl[key]}s` : ''}
                  </small>
                </div>
                <button
                  className={faults[key] ? 'danger' : 'secondary'}
                  onClick={() => {
                    const prompt = 'This will simulate a component failure and may affect request processing. Continue?';
                    if (window.confirm(prompt)) {
                      toggle(key, !faults[key]);
                    }
                  }}
                >
                  {faults[key] ? 'Restore' : 'Inject'}
                </button>
              </div>
            ))}

            <button
              className="textbtn"
              style={{ marginTop: '14px' }}
              onClick={() => {
                setConfirmDialog({
                  title: 'Restore All Faults',
                  message: 'This will remove all active fault simulations and restore normal system operations.',
                  danger: true,
                  onConfirm: resetAllFaults,
                  confirmText: 'Restore All Faults'
                });
              }}
            >
              Restore all faults
            </button>
          </div>
        )}

        {/* ARCHITECTURE TAB */}
        {tab === 'Architecture' && (
          <div className="panel">
            <h2>System architecture</h2>
            <p>Rate decisions are synchronous. Event processing is asynchronous and at-least-once.</p>
            <Architecture health={h} InfoBtn={InfoBtn} />
            <div className="grid three info">
              <div>
                <h3>Decision path <InfoBtn term="Flask Backend" /></h3>
                <p>Flask evaluates policy through one atomic Redis Lua operation, then returns HTTP 200 or 429.</p>
              </div>
              <div>
                <h3>Shared state <InfoBtn term="Redis" /></h3>
                <p>Redis keys use policy ID and SHA-256 of client plus endpoint. Bucket and rolling-window records expire.</p>
              </div>
              <div>
                <h3>Event path <InfoBtn term="RabbitMQ" /></h3>
                <p>Durable RabbitMQ events use manual acknowledgements. Redis SET NX deduplicates worker effects.</p>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* FLOATING HELP BUTTON */}
      <button
        className="floating-help-btn"
        aria-label="Open Navigation Help"
        onClick={() => setShowHelpMenu(true)}
      >
        <HelpCircle size={17} />
        <span>Help</span>
      </button>

      {/* HELP MODAL DIALOG */}
      {showHelpMenu && (
        <div className="modal-backdrop" onClick={() => setShowHelpMenu(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3><Compass size={18} color="#b898d0" /> Where would you like to go?</h3>
              <button className="modal-close" onClick={() => setShowHelpMenu(false)} aria-label="Close Help">
                <X size={16} />
              </button>
            </div>
            <div className="help-list">
              <button
                className="help-item"
                onClick={() => { setTab('Overview'); setShowHelpMenu(false); }}
              >
                <div>
                  <strong>Understand system performance → Overview</strong>
                  <small>Real-time metrics, decision throughput, and health</small>
                </div>
                <ChevronRight size={15} />
              </button>
              <button
                className="help-item"
                onClick={() => { setTab('Configuration'); setShowHelpMenu(false); }}
              >
                <div>
                  <strong>Configure rate limiting → Configuration</strong>
                  <small>Token Bucket vs Sliding Window, burst, and policy keys</small>
                </div>
                <ChevronRight size={15} />
              </button>
              <button
                className="help-item"
                onClick={() => { setTab('Traffic Inspector'); setShowHelpMenu(false); }}
              >
                <div>
                  <strong>Generate and inspect traffic → Traffic Inspector</strong>
                  <small>Live stream of admitted (200) and rejected (429) requests</small>
                </div>
                <ChevronRight size={15} />
              </button>
              <button
                className="help-item"
                onClick={() => { setTab('Fault Injection'); setShowHelpMenu(false); }}
              >
                <div>
                  <strong>Test system failures → Fault Injection</strong>
                  <small>Simulate Redis, worker, latency, and network outages</small>
                </div>
                <ChevronRight size={15} />
              </button>
              <button
                className="help-item"
                onClick={() => { setTab('Architecture'); setShowHelpMenu(false); }}
              >
                <div>
                  <strong>Understand system architecture → Architecture</strong>
                  <small>Synchronous rate evaluation and async durable events</small>
                </div>
                <ChevronRight size={15} />
              </button>
              <button
                className="help-item help-tour-btn"
                onClick={() => { setShowHelpMenu(false); startTour(); }}
              >
                <div>
                  <strong>Take guided tour → Interactive Tour</strong>
                  <small>Step-by-step walkthrough of all features</small>
                </div>
                <Compass size={15} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* FIRST-TIME WELCOME POPUP */}
      {showWelcome && (
        <div className="modal-backdrop" onClick={skipTour}>
          <div className="modal-box tour-welcome" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <div className="eyebrow">GETTING STARTED</div>
                <h3>Welcome to RateLimit Studio!</h3>
              </div>
              <button className="modal-close" onClick={skipTour} aria-label="Skip Tour">
                <X size={16} />
              </button>
            </div>
            <p>
              Welcome to RateLimit Studio! Let's take a quick tour of the system.
            </p>
            <div className="modal-actions" style={{ marginTop: '20px' }}>
              <button className="secondary" onClick={skipTour}>Skip Tour</button>
              <button className="primary" onClick={startTour}>Start Tour</button>
            </div>
          </div>
        </div>
      )}

      {/* GUIDED ONBOARDING TOUR BANNER */}
      {tourStep >= 0 && (
        <div className="tour-banner">
          <div className="tour-banner-header">
            <span className="tour-banner-badge">STEP {tourStep + 1} OF {TOUR_STEPS.length}</span>
            <button className="modal-close" onClick={skipTour} aria-label="End Tour">
              <X size={15} />
            </button>
          </div>
          <h4>{TOUR_STEPS[tourStep].title}</h4>
          <p>
            <strong>{TOUR_STEPS[tourStep].description}</strong>
            <br />
            <span style={{ fontSize: '11.5px', color: '#9f94ab' }}>{TOUR_STEPS[tourStep].detail}</span>
          </p>
          <div className="tour-banner-footer">
            <div className="tour-dots">
              {TOUR_STEPS.map((_, i) => (
                <div key={i} className={'tour-dot ' + (i === tourStep ? 'active' : '')} />
              ))}
            </div>
            <div className="tour-btn-group">
              <button className="secondary" onClick={skipTour} style={{ fontSize: '11px', padding: '6px 10px' }}>
                Skip Tour
              </button>
              {tourStep > 0 && (
                <button className="secondary" onClick={prevTourStep} style={{ fontSize: '11px', padding: '6px 10px' }}>
                  <ChevronLeft size={13} /> Previous
                </button>
              )}
              <button className="primary" onClick={nextTourStep} style={{ fontSize: '11px', padding: '6px 12px' }}>
                {tourStep === TOUR_STEPS.length - 1 ? 'Finish' : 'Next'} <ChevronRight size={13} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* CONTEXTUAL INFO MODAL POPUP */}
      {activeInfo && INFO_TERMS[activeInfo] && (
        <div className="modal-backdrop" onClick={() => setActiveInfo(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3><Info size={17} color="#b898d0" /> {INFO_TERMS[activeInfo].title}</h3>
              <button className="modal-close" onClick={() => setActiveInfo(null)} aria-label="Close Info">
                <X size={16} />
              </button>
            </div>
            <div className="modal-body">
              {INFO_TERMS[activeInfo].content}
            </div>
            <div className="modal-actions">
              <button className="secondary" onClick={() => setActiveInfo(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ACTION CONFIRMATION MODAL */}
      {confirmDialog && (
        <div className="modal-backdrop" onClick={() => setConfirmDialog(null)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>
                <AlertCircle size={18} color={confirmDialog.danger ? '#f18688' : '#b898d0'} />
                {confirmDialog.title}
              </h3>
              <button className="modal-close" onClick={() => setConfirmDialog(null)} aria-label="Cancel">
                <X size={16} />
              </button>
            </div>
            <div className="modal-body">
              {confirmDialog.message}
            </div>
            <div className="modal-actions">
              <button className="secondary" onClick={() => setConfirmDialog(null)}>
                Cancel
              </button>
              <button
                className={confirmDialog.danger ? 'danger' : 'primary'}
                onClick={() => {
                  const cb = confirmDialog.onConfirm;
                  setConfirmDialog(null);
                  if (cb) cb();
                }}
              >
                {confirmDialog.confirmText || 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function TrafficChart({ points = [] }) {
  const w = 760, h = 180, pad = 24;
  const series = [['accepted', '#66d39b'], ['rejected', '#ed7777'], ['errors', '#e4b75c']];
  const max = Math.max(1, ...points.flatMap((x) => series.map(([key]) => x[key] || 0)));
  const line = (key) =>
    points
      .map(
        (x, i) =>
          `${pad + (i * (w - 2 * pad)) / Math.max(1, points.length - 1)},${
            h - pad - ((x[key] || 0) * (h - 2 * pad)) / max
          }`
      )
      .join(' ');

  return (
    <div className="chart">
      <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label="Measured accepted, rejected and error request counts by five second bucket">
        <line x1={pad} y1={h - pad} x2={w - pad} y2={h - pad} stroke="#403a47" />
        <line x1={pad} y1={pad} x2={pad} y2={h - pad} stroke="#403a47" />
        {series.map(([key, color]) => (
          <polyline key={key} points={line(key)} fill="none" stroke={color} strokeWidth="2" />
        ))}
        <text x={pad} y={14} fill="#978e9c" fontSize="10">{max} decisions / 5s</text>
      </svg>
      <div className="chartlegend">
        {series.map(([key, color]) => (
          <span key={key}>
            <i style={{ background: color }} />
            {key}
          </span>
        ))}
        <span className="chartsource">Backend measured · no fabricated data</span>
      </div>
    </div>
  );
}

function Architecture({ health = {}, InfoBtn = () => null }) {
  return (
    <div className="architecture">
      <div className="node client">
        <Radio />
        <b>Client <InfoBtn term="X-Client-ID" /></b>
        <small>identity + request</small>
      </div>
      <div className="arrow">→</div>
      <div className="node limiter">
        <Shield />
        <b>Rate Limiter <InfoBtn term="Token Bucket" /></b>
        <small>atomic decision</small>
      </div>
      <div className="arrow">→</div>
      <div className="node api">
        <Server />
        <b>Flask API <InfoBtn term="Flask Backend" /></b>
        <small>HTTP response</small>
      </div>
      <div className="arrow">→</div>
      <div className="node db">
        <Database />
        <b>App data <InfoBtn term="Redis" /></b>
        <small>history + metrics</small>
      </div>
      <div className="supports">
        <div className="support">
          <Boxes />
          <b>Redis <InfoBtn term="Redis" /></b>
          <small>{health.redis || 'shared state'}</small>
        </div>
        <div className="support async">
          <Workflow />
          <b>RabbitMQ <InfoBtn term="RabbitMQ" /></b>
          <small>async events</small>
        </div>
        <div className="support async">
          <Activity />
          <b>Worker <InfoBtn term="Worker" /></b>
          <small>idempotent sync</small>
        </div>
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
