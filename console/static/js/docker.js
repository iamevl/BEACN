let dockerSnapshots = new Map();
let dockerLoading = false;

function dockerState(container) {
  if (container.health === 'unhealthy') {
    return {className: 'bad', label: 'Unhealthy', dot: '●'};
  }
  if (!container.running) {
    return {className: 'bad', label: container.status || 'Stopped', dot: '●'};
  }
  if (container.health === 'starting') {
    return {className: 'warn', label: 'Starting', dot: '●'};
  }
  if (container.health === 'healthy') {
    return {className: 'good', label: 'Healthy', dot: '●'};
  }
  return {className: 'good', label: 'Running', dot: '●'};
}

function dockerStartedAt(value) {
  if (!value || String(value).startsWith('0001-')) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
}

function dockerUptime(value, running) {
  if (!running || !value || String(value).startsWith('0001-')) return 'Stopped';
  const started = new Date(value);
  if (Number.isNaN(started.getTime())) return '—';
  return uptime(Math.max(0, Math.floor((Date.now() - started.getTime()) / 1000)));
}

function dockerContainerCard(container) {
  const state = dockerState(container);
  const ports = container.ports?.length
    ? container.ports.map(item => `<span class="docker-port">${esc(item)}</span>`).join('')
    : '<span class="muted">No published ports</span>';

  return `
    <article class="docker-card">
      <div class="row-between docker-card-header">
        <div>
          <h3>${esc(container.name)}</h3>
          <div class="docker-image">${esc(container.image)}</div>
        </div>
        <span class="badge ${state.className}">${state.dot} ${esc(state.label)}</span>
      </div>

      <div class="docker-metrics">
        ${box('CPU', `${Number(container.cpu_percent || 0).toFixed(1)}%`)}
        ${box(
          'Memory',
          `${bytes(container.memory_used_bytes)} · ${Number(container.memory_percent || 0).toFixed(1)}%`
        )}
        ${box('Uptime', dockerUptime(container.started_at, container.running))}
        ${box('Restarts', container.restart_count)}
        ${box('Network received', bytes(container.network_rx_bytes))}
        ${box('Network sent', bytes(container.network_tx_bytes))}
      </div>

      <div class="docker-details">
        <div><small>Container ID</small><strong>${esc(container.id)}</strong></div>
        <div><small>Started</small><strong>${esc(dockerStartedAt(container.started_at))}</strong></div>
      </div>

      <div class="docker-ports">${ports}</div>
    </article>`;
}

function renderDocker(payload) {
  const panel = document.getElementById('tab-docker');
  const count = document.getElementById('dockerCount');

  if (!payload?.available) {
    panel.innerHTML = `
      <div class="docker-toolbar">
        <div>
          <h3>Selected device Docker Engine</h3>
          <div class="muted">Docker telemetry is supplied by the selected device's agent.</div>
        </div>
        <button id="dockerRefreshBtn">Retry</button>
      </div>
      <div class="empty">
        Docker monitoring is unavailable.
        <div class="bad" style="margin-top:8px">${esc(payload?.error || 'Unknown Docker error.')}</div>
      </div>`;
    document.getElementById('dockerRefreshBtn')?.addEventListener('click', () => loadDocker(true));
    return;
  }

  const engine = payload.engine || {};
  const containers = payload.containers || [];

  panel.innerHTML = `
    <div class="docker-toolbar">
      <div>
        <h3>${esc(payload.target_hostname || engine.name || 'Selected device')} Docker Engine</h3>
        <div class="muted">
          ${esc(payload.target_ip || '')} · Docker ${esc(engine.server_version || '?')}
          · ${esc(engine.operating_system || '')}
        </div>
      </div>
      <button id="dockerRefreshBtn">Refresh containers</button>
    </div>

    <div class="docker-summary">
      ${hardwareMetric('Containers', String(engine.containers_total || 0), 'Total discovered')}
      ${hardwareMetric('Running', String(engine.containers_running || 0), 'Currently active', 'good-state')}
      ${hardwareMetric('Stopped', String(engine.containers_stopped || 0), 'Not currently running', engine.containers_stopped ? 'warn-state' : '')}
      ${hardwareMetric('Healthy', String(engine.containers_healthy || 0), 'Docker health checks passing', 'good-state')}
      ${hardwareMetric('Unhealthy', String(engine.containers_unhealthy || 0), 'Health checks failing', engine.containers_unhealthy ? 'bad-state' : '')}
    </div>

    <div class="docker-collected">
      Last Docker refresh: ${esc(formatChartTime(payload.collected_at))}
    </div>

    <div class="docker-list">
      ${containers.length
        ? containers.map(dockerContainerCard).join('')
        : '<div class="empty">No Docker containers were found.</div>'}
    </div>`;

  document.getElementById('dockerRefreshBtn')?.addEventListener('click', () => loadDocker(true));
}

async function loadDocker(force = false) {
  if (dockerLoading) return;

  const device = selected();
  const panel = document.getElementById('tab-docker');

  if (!device) {
    panel.innerHTML = '<div class="empty">Select a device.</div>';
    return;
  }

  const cacheKey = device.ip;
  if (dockerSnapshots.has(cacheKey) && !force) {
    renderDocker(dockerSnapshots.get(cacheKey));
    return;
  }

  dockerLoading = true;
  if (activeTab === 'docker') {
    panel.innerHTML = `<div class="empty">Reading Docker telemetry from ${esc(device.hostname || device.ip)}…</div>`;
  }

  try {
    const response = await fetch(
      `/api/docker/${encodeURIComponent(device.ip)}`,
      {cache: 'no-store'}
    );
    const contentType = response.headers.get('content-type') || '';
    if (!response.ok || !contentType.includes('application/json')) {
      throw new Error(`Docker API returned HTTP ${response.status}.`);
    }
    const payload = await response.json();
    dockerSnapshots.set(cacheKey, payload);
    renderDocker(payload);
  } catch (error) {
    const payload = {
      available: false,
      target_ip: device.ip,
      target_hostname: device.hostname || device.ip,
      error: error.message
    };
    dockerSnapshots.set(cacheKey, payload);
    renderDocker(payload);
  } finally {
    dockerLoading = false;
  }
}
