let devices = [];
let infrastructure = [];
  let activeDeviceTypeFilter = null;
  let current = null;
  let activeTab = 'overview';
  let liveEnabled = true;
  let liveTimer = null;
  let telemetryPoints = [];
  let telemetryRange = '1h';
  const chartModels = new Map();

  const select = document.getElementById('deviceSelect');
  const output = document.getElementById('output');
  const status = document.getElementById('status');

  const esc = value => String(value ?? '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#039;');

function hasAgent(device, agent = null) {
  return Boolean(
    device?.agent_available ||
    device?.agent ||
    device?.agent_version ||
    agent?.agent ||
    agent?.version
  );
}

function componentStatusIcon(device, agent = null) {
  return hasAgent(device, agent)
    ? 'agent.svg'
    : 'discovery.svg';
}

function renderComponentStatus(device, agent = null) {
  const heroStatus = document.getElementById('heroStatus');

  if (!heroStatus) {
    return;
  }

  if (!device) {
    heroStatus.innerHTML = `
      <span class="component-status unknown">
        Unknown
      </span>
    `;
    return;
  }

  const online = Boolean(device.is_online);
  const agentDevice = hasAgent(device, agent);
  const component = agentDevice ? 'Agent' : 'Discovery';
  const state = online ? 'Online' : 'Offline';
  const stateClass = online ? 'online' : 'offline';
  const icon = componentStatusIcon(device, agent);

  heroStatus.innerHTML = `
    <span class="component-status ${stateClass}">
      <img
        class="component-status-icon"
        src="/static/branding/icons/${icon}"
        alt=""
        aria-hidden="true"
      >
      <span>${component} ${state}</span>
    </span>
  `;
}

const selected = () => devices.find(device => device.ip === select.value);

  function bytes(value) {
    value = Number(value);
    if (!Number.isFinite(value)) return '—';
    const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
    let index = 0;
    while (value >= 1000 && index < units.length - 1) {
      value /= 1000;
      index += 1;
    }
    return `${value.toFixed(index >= 3 ? 1 : 0)} ${units[index]}`;
  }

  function uptime(seconds) {
    if (seconds == null) return '—';
    const value = Number(seconds);
    const days = Math.floor(value / 86400);
    const hours = Math.floor((value % 86400) / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    return [
      days ? `${days}d` : '',
      hours || days ? `${hours}h` : '',
      `${minutes}m`,
    ].filter(Boolean).join(' ');
  }

  function rate(bitsPerSecond) {
    return bitsPerSecond
      ? `${(bitsPerSecond / 1e6).toFixed(1)} Mbps`
      : 'n/a';
  }

  function severityClass(percent) {
    const value = Number(percent) || 0;
    return value >= 95 ? 'danger' : value >= 90 ? 'warning' : '';
  }

  function bar(percent, capacity = false) {
    const value = Math.max(0, Math.min(100, Number(percent) || 0));
    const track = capacity ? 'capacity-track' : 'progress-track';
    const fill = capacity ? 'capacity-fill' : 'progress-fill';
    return `<div class="${track}"><div class="${fill} ${severityClass(value)}" style="width:${value}%"></div></div>`;
  }

  function box(label, value) {
    return `<div class="info-box"><small>${esc(label)}</small><strong>${esc(value || '—')}</strong></div>`;
  }

  function adapterPresentation(name) {
    const value = String(name || '').toLowerCase();
    if (value.includes('tailscale')) return {icon: '🔒', label: 'Tailscale'};
    if (value.includes('proton')) return {icon: '🛡️', label: 'ProtonVPN'};
    if (value.includes('wi-fi') || value.includes('wifi')) return {icon: '📶', label: name};
    if (value.includes('ethernet')) return {icon: '🌐', label: name};
    return {icon: '🔌', label: name};
  }

  function finite(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function hardwareDevices(hardware) {
    const result = [];
    const visit = item => {
      if (!item || typeof item !== 'object') return;
      result.push(item);
      (item.subHardware || []).forEach(visit);
    };
    (hardware?.hardware || []).forEach(visit);
    return result;
  }

  function sensorsFor(device, type, pattern = null) {
    return (device?.sensors || []).filter(sensor => {
      if (String(sensor.type || '').toLowerCase() !== String(type).toLowerCase()) return false;
      if (finite(sensor.value) == null) return false;
      return !pattern || pattern.test(String(sensor.name || ''));
    });
  }

  function firstSensor(device, type, names = []) {
    const sensors = sensorsFor(device, type);
    for (const name of names) {
      const sensor = sensors.find(item =>
        String(item.name || '').toLowerCase() === String(name).toLowerCase()
      );
      if (sensor) return sensor;
    }
    return sensors[0] || null;
  }

  function temperatureState(value, warning = 75, critical = 90) {
    const number = finite(value);
    if (number == null) return '';
    if (number >= critical) return 'bad-state';
    if (number >= warning) return 'warn-state';
    return 'good-state';
  }

  function utilisationState(value, warning = 70, critical = 90) {
    const number = finite(value);
    if (number == null) return '';
    if (number >= critical) return 'bad-state';
    if (number >= warning) return 'warn-state';
    return 'good-state';
  }

  function latestTelemetryValue(key) {
    for (let index = telemetryPoints.length - 1; index >= 0; index -= 1) {
      const value = finite(telemetryPoints[index]?.[key]);
      if (value != null) return value;
    }
    return null;
  }

  function metricChip(value, unit = '', warning = null, critical = null, idleBelow = null) {
    const number = finite(value);
    if (number == null) return '<span class="metric-chip">Waiting</span>';
    if (idleBelow != null && number <= idleBelow) {
      return '<span class="metric-chip">Idle</span>';
    }

    let state = 'good';
    if (critical != null && number >= critical) state = 'bad';
    else if (warning != null && number >= warning) state = 'warn';

    const decimals = Math.abs(number) < 10 ? 1 : 0;
    return `<span class="metric-chip ${state}">${number.toFixed(decimals)}${esc(unit)}</span>`;
  }

  function formatChartTime(value) {
    if (!value) return 'Unknown time';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return date.toLocaleString([], {
      day: '2-digit',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit'
    });
  }

  function hardwareMetric(label, value, secondary = '', state = '') {
    return `<div class="hardware-metric ${state}">
      <small>${esc(label)}</small>
      <strong>${esc(value)}</strong>
      <div class="secondary">${esc(secondary)}</div>
    </div>`;
  }

  function sensorCard(sensor, unit = '', warning = null, critical = null) {
    const value = finite(sensor?.value);
    if (value == null) return '';
    const minimum = finite(sensor.minimum ?? sensor.min);
    const maximum = finite(sensor.maximum ?? sensor.max);
    const state = warning == null ? '' : temperatureState(value, warning, critical);
    const range = minimum != null && maximum != null
      ? `Min ${minimum.toFixed(1)}${unit} · Max ${maximum.toFixed(1)}${unit}`
      : '';
    return `<div class="sensor-card ${state}">
      <small>${esc(sensor.name || 'Sensor')}</small>
      <strong>${value.toFixed(1)}${esc(unit)}</strong>
      <div class="range">${esc(range)}</div>
    </div>`;
  }

