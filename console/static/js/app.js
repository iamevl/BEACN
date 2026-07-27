let devices = [];
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

  function renderHardware(device, agent) {
    const panel = document.getElementById('tab-hardware');
    const hardware = agent?.hardware;

    if (!device) {
      panel.innerHTML = '<div class="empty">Select a device.</div>';
      return;
    }

    if (!hardware?.available) {
      panel.innerHTML = `<div class="empty">
        Hardware monitoring is unavailable for this device.
        ${hardware?.error ? `<div class="bad" style="margin-top:8px">${esc(hardware.error)}</div>` : ''}
      </div>`;
      return;
    }

    const devices = hardwareDevices(hardware);
    const cpuDevice = devices.find(item => String(item.type).toLowerCase() === 'cpu');
    const ramDevice = devices.find(item => item.identifier === '/ram')
      || devices.find(item => String(item.type).toLowerCase() === 'memory' && !String(item.identifier).includes('/dimm/'));
    const dimmDevices = devices.filter(item =>
      String(item.identifier || '').includes('/memory/dimm/')
      || /dimm/i.test(String(item.name || ''))
    );
    const gpuDevices = devices.filter(item => /^gpu/i.test(String(item.type || '')));

    const cpuTemp = finite(hardware.summary?.cpuTemperatureC);
    const cpuPower = finite(hardware.summary?.cpuPowerW);
    const cpuLoad = firstSensor(cpuDevice, 'Load', ['CPU Total']);
    const ramLoad = firstSensor(ramDevice, 'Load', ['Memory']);
    const clocks = sensorsFor(cpuDevice, 'Clock', /^(P-Core|E-Core|CPU Core)/i);
    const fastestClock = clocks.reduce((max, sensor) => Math.max(max, finite(sensor.value) || 0), 0);
    const coreTemps = sensorsFor(cpuDevice, 'Temperature', /^(P-Core|E-Core|CPU Core) #?\d+$/i);
    const dimmTemps = dimmDevices.flatMap(item =>
      sensorsFor(item, 'Temperature', /^DIMM #\d+$/i)
    );
    const fans = Array.isArray(hardware.summary?.fans) ? hardware.summary.fans : [];

    const gpuCards = gpuDevices.map(gpu => {
      const load = firstSensor(gpu, 'Load', ['GPU Core', 'D3D 3D', 'GPU Total']);
      const clock = firstSensor(gpu, 'Clock', ['GPU Core']);
      const power = firstSensor(gpu, 'Power', ['GPU Power']);
      const temp = firstSensor(gpu, 'Temperature', ['GPU Core', 'GPU Hot Spot']);
      const details = [
        load ? `${finite(load.value).toFixed(1)}% load` : '',
        clock ? `${finite(clock.value).toFixed(0)} MHz` : '',
        power ? `${finite(power.value).toFixed(1)} W` : '',
        temp ? `${finite(temp.value).toFixed(1)}°C` : ''
      ].filter(Boolean).join(' · ');
      return `<div class="hardware-device">
        <div class="row-between"><strong>${esc(gpu.name)}</strong><span class="badge">${esc(gpu.type)}</span></div>
        <div class="muted" style="margin-top:7px">${esc(details || 'No live GPU sensors reported.')}</div>
      </div>`;
    }).join('');

    panel.innerHTML = `
      <div class="row-between" style="margin-bottom:14px">
        <div>
          <h3 style="margin-bottom:4px">Live hardware health</h3>
          <div class="muted">${esc(hardware.provider || 'Hardware provider')}${hardware.providerVersion ? ` · v${esc(hardware.providerVersion)}` : ''}</div>
        </div>
        <span class="badge good">Available</span>
      </div>
      <div class="hardware-summary">
        ${hardwareMetric('CPU temperature', cpuTemp == null ? '—' : `${cpuTemp.toFixed(1)}°C`, cpuDevice?.name || 'CPU package', temperatureState(cpuTemp))}
        ${hardwareMetric('CPU power', cpuPower == null ? '—' : `${cpuPower.toFixed(1)} W`, 'Package power')}
        ${hardwareMetric('CPU load', cpuLoad ? `${finite(cpuLoad.value).toFixed(1)}%` : '—', 'Current utilisation', utilisationState(cpuLoad?.value))}
        ${hardwareMetric('Fastest core', fastestClock ? `${(fastestClock / 1000).toFixed(2)} GHz` : '—', `${clocks.length} clock sensors`)}
        ${hardwareMetric('Memory load', ramLoad ? `${finite(ramLoad.value).toFixed(1)}%` : '—', `${dimmDevices.length} DIMM devices`, utilisationState(ramLoad?.value))}
        ${hardwareMetric('Cooling fans', String(fans.length), fans.length ? 'RPM sensors detected' : 'No fan sensors exposed')}
      </div>
      <div class="sensor-group">
        <h3>CPU core temperatures</h3>
        ${coreTemps.length ? `<div class="sensor-grid">${coreTemps.map(sensor => sensorCard(sensor, '°C', 80, 95)).join('')}</div>` : '<div class="empty">No individual CPU core temperatures reported.</div>'}
      </div>
      <div class="sensor-group">
        <h3>Memory modules</h3>
        ${dimmTemps.length ? `<div class="sensor-grid">${dimmTemps.map(sensor => sensorCard(sensor, '°C', 70, 85)).join('')}</div>` : '<div class="empty">No DIMM temperatures reported.</div>'}
      </div>
      <div class="sensor-group">
        <h3>Graphics</h3>
        ${gpuCards || '<div class="empty">No GPU hardware reported.</div>'}
      </div>
      <div class="sensor-group">
        <h3>Fans</h3>
        ${fans.length ? `<div class="sensor-grid">${fans.map(fan => {
          const rpm = finite(fan.rpm ?? fan.value);
          return `<div class="sensor-card"><small>${esc(fan.name || 'Fan')}</small><strong>${rpm == null ? '—' : rpm.toFixed(0)} RPM</strong></div>`;
        }).join('')}</div>` : '<div class="empty">This system does not expose fan RPM data through LibreHardwareMonitor.</div>'}
      </div>
      <div class="sensor-group">
        <div class="row-between">
          <h3>Performance history</h3>
          <div class="history-ranges">
            ${['1h','6h','24h','7d'].map(range => `<button class="range-btn ${telemetryRange === range ? 'active' : ''}" data-range="${range}">${range}</button>`).join('')}
          </div>
        </div>
        <div class="history-chart-grid">
          <div class="chart-card">
            <div class="chart-card-title">
              <h3>CPU temperature</h3>
              <span id="cpuTempChip">${metricChip(latestTelemetryValue('cpu_temperature_c'), '°C', 75, 90)}</span>
            </div>
            <div class="chart-wrap"><canvas id="cpuTempChart"></canvas></div>
          </div>
          <div class="chart-card">
            <div class="chart-card-title">
              <h3>CPU power</h3>
              <span id="cpuPowerChip">${metricChip(latestTelemetryValue('cpu_power_w'), ' W')}</span>
            </div>
            <div class="chart-wrap"><canvas id="cpuPowerChart"></canvas></div>
          </div>
          <div class="chart-card">
            <div class="chart-card-title">
              <h3>GPU load</h3>
              <span id="gpuLoadChip">${metricChip(latestTelemetryValue('gpu_load_percent'), '%', 75, 95, 0.5)}</span>
            </div>
            <div class="chart-wrap"><canvas id="gpuLoadChart"></canvas></div>
          </div>
          <div class="chart-card">
            <div class="chart-card-title">
              <h3>Memory load</h3>
              <span id="memoryChip">${metricChip(latestTelemetryValue('memory_percent'), '%', 70, 90)}</span>
            </div>
            <div class="chart-wrap"><canvas id="memoryHistoryChart"></canvas></div>
          </div>
        </div>
      </div>`;
  }

  function render(device, agent) {
    document.getElementById('heroName').textContent =
      device?.hostname || device?.ip || 'No device selected';

    const os = agent?.operating_system;
    const processor = agent?.processor;
    const performance = agent?.performance;
    const capabilities = agent?.agent?.capabilities || [];

    renderHardware(device, agent);

    document.getElementById('heroSubtitle').textContent = os
      ? `${os.product_name}${os.display_version ? ` · ${os.display_version}` : ''}`
      : (device?.ip || '—');

    document.getElementById('heroStatus').innerHTML = device?.is_online
      ? '<span class="good">● Online</span>'
      : '<span class="bad">● Offline</span>';

    document.getElementById('tab-overview').innerHTML = device ? `
      <div class="hero-grid">
        <div class="hero-box">
          <small>Operating system</small>
          <strong>${esc(os?.product_name || 'Agent not available')}</strong>
          <div class="secondary">${esc(os?.display_version || '')}${os?.build ? ` · build ${esc(os.build)}` : ''}</div>
        </div>
        <div class="hero-box">
          <small>Processor</small>
          <strong>${esc(processor?.model || 'Unknown')}</strong>
          <div class="secondary">${processor ? `${processor.physical_cores} physical · ${processor.logical_cores} logical` : ''}</div>
        </div>
        <div class="hero-box">
          <small>Installed memory</small>
          <strong>${performance ? bytes(performance.memory_total_bytes) : '—'}</strong>
          <div class="secondary">${performance ? `${Number(performance.memory_percent || 0).toFixed(1)}% currently used` : ''}</div>
        </div>
      </div>
      <div class="info-grid">
        ${box('IP address', device.ip)}
        ${box('Hostname', device.hostname)}
        ${box('MAC address', device.mac)}
        ${box('Vendor', device.vendor)}
        ${box('Architecture', os?.architecture)}
        ${box('Uptime', uptime(agent?.device?.uptime_seconds ?? device.uptime_seconds))}
        ${box('Agent', device.agent_available ? `Connected · v${device.agent_version || '?'}` : 'Not detected')}
        ${box('Python', agent?.device?.python_version)}
        ${box('iperf3', device.iperf_available ? 'Available' : 'Not detected')}
        ${box('Last seen', device.last_seen)}
      </div>
      <h3 style="margin-top:18px">Capabilities</h3>
      <div class="capabilities">
        ${capabilities.length
          ? capabilities.map(item => `<span class="badge">${esc(item.replaceAll('_', ' '))}</span>`).join('')
          : '<span class="muted">No capabilities reported.</span>'}
      </div>
    ` : '<div class="empty">Select a device.</div>';

    const disks = agent?.disks || [];
    document.getElementById('tab-storage').innerHTML = disks.length
      ? disks.map(disk => {
          const percent = Number(disk.percent) || 0;
          const health = percent >= 95
            ? '<span class="bad">Critical</span>'
            : percent >= 90
              ? '<span class="warn">Nearly full</span>'
              : '<span class="good">Healthy</span>';

          const warning = percent >= 95
            ? `<div class="bad" style="margin-top:9px">⚠ Only ${bytes(disk.free_bytes)} remains.</div>`
            : percent >= 90
              ? `<div class="warn" style="margin-top:9px">Low free space: ${bytes(disk.free_bytes)} remains.</div>`
              : '';

          return `
            <div class="disk-card">
              <div class="row-between">
                <strong>${esc(disk.mountpoint || disk.device)}</strong>
                <span>${percent.toFixed(1)}% · ${health}</span>
              </div>
              <div class="muted" style="margin-top:5px">
                ${esc(disk.filesystem || 'Unknown')} ·
                ${bytes(disk.used_bytes)} used of ${bytes(disk.total_bytes)}
              </div>
              ${bar(percent, true)}
              <div class="row-between" style="margin-top:8px">
                <small>Used ${bytes(disk.used_bytes)}</small>
                <small>Free ${bytes(disk.free_bytes)}</small>
              </div>
              ${warning}
            </div>`;
        }).join('')
      : '<div class="empty">No storage inventory reported.</div>';

    const adapters = (agent?.network_adapters || []).filter(adapter =>
      adapter.is_up &&
      !/loopback|default switch|wsl/i.test(adapter.name) &&
      (adapter.addresses || []).some(address =>
        address.family === 'IPv4' &&
        !String(address.address).startsWith('169.254.') &&
        address.address !== '127.0.0.1'
      )
    );

    document.getElementById('tab-network').innerHTML = adapters.length
      ? adapters.map(adapter => {
          const presentation = adapterPresentation(adapter.name);
          const ips = (adapter.addresses || [])
            .filter(address =>
              address.family === 'IPv4' &&
              !String(address.address).startsWith('169.254.')
            )
            .map(address => esc(address.address))
            .join(' · ');

          const speed = adapter.speed_mbps && adapter.speed_mbps < 4000
            ? `${adapter.speed_mbps} Mbps`
            : 'Virtual adapter';

          return `
            <div class="adapter-card">
              <div class="row-between">
                <div class="adapter-title">
                  <span class="adapter-icon">${presentation.icon}</span>
                  <strong>${esc(presentation.label)}</strong>
                </div>
                <span class="good">Up</span>
              </div>
              <div style="margin-top:10px">${ips || 'No IPv4 address'}</div>
              <div class="muted" style="margin-top:5px">${speed} · MTU ${adapter.mtu ?? '—'}</div>
            </div>`;
        }).join('')
      : '<div class="empty">No active network adapters reported.</div>';

    const cpu = performance?.cpu_percent ?? device?.cpu_percent;
    const memory = performance?.memory_percent ?? device?.memory_percent;

    document.getElementById('tab-performance').innerHTML = `
      <div class="live-controls">
        <button id="liveToggle">${liveEnabled ? 'Pause live monitoring' : 'Resume live monitoring'}</button>
        <span class="live-indicator">
          <span class="pulse ${liveEnabled ? '' : 'paused'}"></span>
          ${liveEnabled ? 'Live, polling every 2 seconds' : 'Monitoring paused'}
        </span>
      </div>
      <div class="progress-block">
        <div class="progress-label"><strong>CPU utilisation</strong><span>${Number(cpu || 0).toFixed(1)}%</span></div>
        ${bar(cpu)}
      </div>
      <div class="progress-block">
        <div class="progress-label"><strong>Memory utilisation</strong><span>${Number(memory || 0).toFixed(1)}%</span></div>
        ${bar(memory)}
      </div>
      <div class="info-grid">
        ${box('Processor', processor?.model)}
        ${box('Physical cores', processor?.physical_cores)}
        ${box('Logical cores', processor?.logical_cores)}
        ${box('Current frequency', processor?.frequency_current_mhz ? `${(processor.frequency_current_mhz / 1000).toFixed(2)} GHz` : '—')}
        ${box('Installed memory', performance?.memory_total_bytes ? bytes(performance.memory_total_bytes) : '—')}
        ${box('Available memory', performance?.memory_available_bytes ? bytes(performance.memory_available_bytes) : '—')}
      </div>
      <div class="chart-card" style="margin-top:14px">
        <div class="row-between">
          <h3>CPU and memory history</h3>
          <div class="history-ranges">
            ${['1h','6h','24h','7d'].map(range => `<button class="range-btn ${telemetryRange === range ? 'active' : ''}" data-range="${range}">${range}</button>`).join('')}
          </div>
        </div>
        <div class="chart-wrap"><canvas id="telemetryChart"></canvas></div>
      </div>`;

    document.getElementById('liveToggle')?.addEventListener('click', () => {
      liveEnabled = !liveEnabled;
      configureLivePolling();
      render(current?.device || device, current?.agent || agent);
      drawTelemetryChart();
    });

    const services = Object.entries(agent?.services || {});
    document.getElementById('tab-services').innerHTML = services.length
      ? services.map(([name, service]) => `
          <div class="service-card">
            <div class="row-between">
              <strong>${esc(name)}</strong>
              ${service.running
                ? '<span class="good">Running</span>'
                : '<span class="bad">Stopped</span>'}
            </div>
            <div class="muted" style="margin-top:5px">
              ${service.port ? `TCP ${esc(service.port)}` : 'No port reported'}
            </div>
          </div>`).join('')
      : '<div class="empty">No services reported.</div>';

    bindRangeButtons();
    drawHardwareHistoryCharts();
  }

  async function loadTelemetry() {
    const device = selected();
    if (!device?.agent_available) {
      telemetryPoints = [];
      drawTelemetryChart();
      return;
    }

    const response = await fetch(
      `/api/telemetry/${encodeURIComponent(device.ip)}?range=${telemetryRange}&limit=1000`
    );
    const payload = await response.json();
    telemetryPoints = payload.ok ? payload.points : [];
    drawTelemetryChart();
    drawHardwareHistoryCharts();
  }

  function drawTelemetryChart() {
    const canvas = document.getElementById('telemetryChart');
    if (!canvas) return;

    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(rect.width * ratio));
    canvas.height = Math.max(1, Math.floor(rect.height * ratio));

    const ctx = canvas.getContext('2d');
    ctx.scale(ratio, ratio);

    const width = rect.width;
    const height = rect.height;
    const left = 36;
    const right = 12;
    const top = 12;
    const bottom = 26;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;

    ctx.clearRect(0, 0, width, height);
    ctx.font = '11px system-ui';
    ctx.fillStyle = '#95a4c4';
    ctx.strokeStyle = '#24324e';
    ctx.lineWidth = 1;

    [0, 25, 50, 75, 100].forEach(value => {
      const y = top + plotHeight - (value / 100) * plotHeight;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(left + plotWidth, y);
      ctx.stroke();
      ctx.fillText(`${value}%`, 2, y + 4);
    });

    if (!telemetryPoints.length) {
      ctx.fillText('No telemetry history yet. Leave live monitoring running for a moment.', left + 12, top + 28);
      return;
    }

    const drawLine = (key, stroke) => {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = 2;
      ctx.beginPath();

      telemetryPoints.forEach((point, index) => {
        const x = left + (
          telemetryPoints.length === 1
            ? plotWidth
            : (index / (telemetryPoints.length - 1)) * plotWidth
        );
        const value = Math.max(0, Math.min(100, Number(point[key]) || 0));
        const y = top + plotHeight - (value / 100) * plotHeight;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });

      ctx.stroke();
    };

    drawLine('cpu_percent', '#6ee7b7');
    drawLine('memory_percent', '#fbbf24');

    ctx.fillStyle = '#6ee7b7';
    ctx.fillRect(left, height - 13, 10, 3);
    ctx.fillStyle = '#95a4c4';
    ctx.fillText('CPU', left + 15, height - 8);

    ctx.fillStyle = '#fbbf24';
    ctx.fillRect(left + 60, height - 13, 10, 3);
    ctx.fillStyle = '#95a4c4';
    ctx.fillText('Memory', left + 75, height - 8);
  }

  function movingAverage(values, windowSize = 1) {
    if (windowSize <= 1) return values;
    return values.map((value, index) => {
      const start = Math.max(0, index - windowSize + 1);
      const sample = values.slice(start, index + 1);
      return sample.reduce((total, item) => total + item, 0) / sample.length;
    });
  }

  function bindChartTooltip(canvas) {
    if (!canvas || canvas.dataset.tooltipBound === '1') return;
    canvas.dataset.tooltipBound = '1';

    const wrap = canvas.closest('.chart-wrap');
    if (!wrap) return;

    let tooltip = wrap.querySelector('.chart-tooltip');
    if (!tooltip) {
      tooltip = document.createElement('div');
      tooltip.className = 'chart-tooltip';
      wrap.appendChild(tooltip);
    }

    canvas.addEventListener('mousemove', event => {
      const model = chartModels.get(canvas.id);
      if (!model?.points?.length) {
        tooltip.style.display = 'none';
        return;
      }

      const rect = canvas.getBoundingClientRect();
      const mouseX = event.clientX - rect.left;
      const boundedX = Math.max(model.left, Math.min(model.left + model.plotWidth, mouseX));
      const fraction = model.plotWidth > 0
        ? (boundedX - model.left) / model.plotWidth
        : 0;
      const index = Math.max(
        0,
        Math.min(model.points.length - 1, Math.round(fraction * (model.points.length - 1)))
      );

      const point = model.points[index];
      const value = model.values[index];
      const x = model.left + (
        model.points.length === 1
          ? model.plotWidth
          : (index / (model.points.length - 1)) * model.plotWidth
      );
      const y = model.top + model.plotHeight
        - ((value - model.min) / (model.max - model.min)) * model.plotHeight;

      tooltip.innerHTML = `
        <strong>${esc(model.label)}: ${Number(value).toFixed(model.decimals)}${esc(model.unit)}</strong>
        <span>${esc(formatChartTime(point.created_at))}</span>`;
      tooltip.style.left = `${Math.max(75, Math.min(rect.width - 75, x))}px`;
      tooltip.style.top = `${Math.max(44, y)}px`;
      tooltip.style.display = 'block';
    });

    canvas.addEventListener('mouseleave', () => {
      tooltip.style.display = 'none';
    });
  }

  function drawSeriesChart(canvasId, key, label, unit, options = {}) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;

    const points = telemetryPoints.filter(point => finite(point[key]) != null);
    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.floor(rect.width * ratio));
    canvas.height = Math.max(1, Math.floor(rect.height * ratio));

    const ctx = canvas.getContext('2d');
    ctx.scale(ratio, ratio);

    const width = rect.width;
    const height = rect.height;
    const left = 48;
    const right = 12;
    const top = 14;
    const bottom = 24;
    const plotWidth = width - left - right;
    const plotHeight = height - top - bottom;

    ctx.clearRect(0, 0, width, height);
    ctx.font = '11px system-ui';
    ctx.fillStyle = '#95a4c4';
    ctx.strokeStyle = '#24324e';
    ctx.lineWidth = 1;

    if (!points.length) {
      ctx.fillText(`No ${label.toLowerCase()} history yet.`, left, top + 25);
      return;
    }

    const rawValues = points.map(point => finite(point[key])).filter(value => value != null);
    const values = movingAverage(rawValues, options.smoothWindow || 1);
    const actualMin = Math.min(...values);
    const actualMax = Math.max(...values);

    if (options.inactiveBelow != null && actualMax <= options.inactiveBelow) {
      ctx.fillText(options.inactiveMessage || 'No meaningful activity detected.', left, top + 25);
      return;
    }

    const spread = Math.max(actualMax - actualMin, options.minimumSpread || 1);
    const padding = Math.max(spread * 0.18, options.minimumPadding || 1);
    let min = options.zeroBased ? 0 : actualMin - padding;
    let max = actualMax + padding;

    if (options.floor != null) min = Math.max(options.floor, min);
    if (options.ceiling != null) max = Math.min(options.ceiling, max);
    if (max <= min) max = min + (options.minimumSpread || 1);

    [0, .25, .5, .75, 1].forEach(fraction => {
      const value = min + (max - min) * fraction;
      const y = top + plotHeight - fraction * plotHeight;
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(left + plotWidth, y);
      ctx.stroke();
      const decimals = Math.abs(max - min) < 20 ? 1 : 0;
      ctx.fillText(`${value.toFixed(decimals)}${unit}`, 2, y + 4);
    });

    ctx.strokeStyle = '#6ee7b7';
    ctx.lineWidth = 2;
    ctx.beginPath();

    values.forEach((value, index) => {
      const x = left + (
        values.length === 1
          ? plotWidth
          : (index / (values.length - 1)) * plotWidth
      );
      const y = top + plotHeight - ((value - min) / (max - min)) * plotHeight;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });

    ctx.stroke();

    chartModels.set(canvasId, {
      points,
      values,
      min,
      max,
      left,
      top,
      plotWidth,
      plotHeight,
      label,
      unit,
      decimals: Math.abs(max - min) < 20 ? 1 : 0
    });
    bindChartTooltip(canvas);
  }

  function drawHardwareHistoryCharts() {
    drawSeriesChart('cpuTempChart', 'cpu_temperature_c', 'CPU temperature', '°C', {
      floor: 0,
      minimumSpread: 6,
      minimumPadding: 2
    });
    drawSeriesChart('cpuPowerChart', 'cpu_power_w', 'CPU power', 'W', {
      floor: 0,
      minimumSpread: 8,
      minimumPadding: 2
    });
    drawSeriesChart('gpuLoadChart', 'gpu_load_percent', 'GPU load', '%', {
      floor: 0,
      ceiling: 100,
      zeroBased: true,
      minimumSpread: 5,
      inactiveBelow: 0.5,
      inactiveMessage: 'GPU is currently idle.'
    });
    drawSeriesChart('memoryHistoryChart', 'memory_percent', 'Memory load', '%', {
      floor: 0,
      ceiling: 100,
      minimumSpread: 8,
      minimumPadding: 2,
      smoothWindow: 3
    });
  }

  function bindRangeButtons() {
    document.querySelectorAll('.range-btn').forEach(button => {
      button.addEventListener('click', async () => {
        telemetryRange = button.dataset.range;
        await loadTelemetry();
        render(current?.device, current?.agent);
        drawTelemetryChart();
        drawHardwareHistoryCharts();
      });
    });
  }

  async function history(device) {
    const panel = document.getElementById('tab-history');
    if (!device) {
      panel.innerHTML = '<div class="empty">Select a device.</div>';
      return;
    }

    const response = await fetch(
      `/api/results?target=${encodeURIComponent(device.ip)}`
    );
    const payload = await response.json();

    panel.innerHTML = payload.results.length
      ? `<table>
          <thead>
            <tr>
              <th>Time</th>
              <th>Direction</th>
              <th>Throughput</th>
              <th>Retransmits</th>
            </tr>
          </thead>
          <tbody>
            ${payload.results.map(result => `
              <tr>
                <td>${esc(result.created_at)}</td>
                <td>${esc(result.direction)}</td>
                <td>${esc(rate(result.bits_per_second))}</td>
                <td>${esc(result.retransmits ?? 'n/a')}</td>
              </tr>`).join('')}
          </tbody>
        </table>`
      : '<div class="empty">No saved iperf3 results for this device.</div>';
  }

  async function details(refresh = false) {
    const device = selected();
    if (!device) {
      current = null;
      render(null, null);
      return;
    }

    const response = await fetch(
      `/api/device/${encodeURIComponent(device.ip)}?refresh=${refresh ? '1' : '0'}`
    );

    const contentType = response.headers.get('content-type') || '';
    if (!response.ok || !contentType.includes('application/json')) {
      const body = await response.text();
      const summary = body.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
      throw new Error(
        `Agent refresh failed (${response.status})${summary ? `: ${summary.slice(0, 140)}` : ''}`
      );
    }

    const payload = await response.json();

    current = payload.ok
      ? payload
      : {device, agent: null};

    render(current.device || device, current.agent);
    history(current.device || device);
    await loadTelemetry();

    const resolved = current.device || device;
    document.getElementById('refreshAgentBtn').disabled = !resolved.agent_available;
    document.getElementById('iperfBtn').disabled = !resolved.iperf_available;
    document.getElementById('iperfReverseBtn').disabled = !resolved.iperf_available;
  }

  function configureLivePolling() {
    if (liveTimer) {
      clearInterval(liveTimer);
      liveTimer = null;
    }

    if (!liveEnabled || !['performance', 'hardware'].includes(activeTab)) return;

    liveTimer = setInterval(async () => {
      if (document.hidden) return;
      const device = selected();
      if (!device?.agent_available) return;

      try {
        await details(true);
      } catch (error) {
        status.textContent = `Live monitoring: ${error.message}`;
      }
    }, 2000);
  }

  async function refreshDevices() {
    const response = await fetch('/api/devices');
    const payload = await response.json();
    const oldValue = select.value;

    devices = payload.devices;
    select.innerHTML = devices.map(device => `
      <option value="${esc(device.ip)}">
        ${esc(
          (device.hostname ? `${device.hostname} · ` : '') +
          device.ip +
          (device.agent_available ? ' · Agent' : '') +
          (device.iperf_available ? ' · iperf3' : '')
        )}
      </option>`).join('');

    if (devices.some(device => device.ip === oldValue)) {
      select.value = oldValue;
    }

    document.getElementById('onlineCount').textContent =
      devices.filter(device => device.is_online).length;
    document.getElementById('agentCount').textContent =
      devices.filter(device => device.agent_available).length;
    document.getElementById('iperfCount').textContent =
      devices.filter(device => device.iperf_available).length;
    document.getElementById('lastRefresh').textContent =
      new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'});

    status.textContent = payload.scan.running
      ? 'Network scan running…'
      : (payload.scan.last_error || '');

    await details(false);
  }

  async function post(url, body) {
    status.textContent = 'Working…';
    output.textContent = 'Running command…';

    const response = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {}),
    });
    const payload = await response.json();

    if (!response.ok) {
      throw new Error(payload.error || 'Request failed');
    }

    status.textContent =
      payload.message ||
      (payload.ok ? 'Complete.' : 'Command returned an error.');

    return payload;
  }

  document.querySelectorAll('.tab').forEach(button => {
    button.addEventListener('click', () => {
      document.querySelectorAll('.tab')
        .forEach(tab => tab.classList.remove('active'));
      document.querySelectorAll('.tab-panel')
        .forEach(panel => panel.classList.remove('active'));

      button.classList.add('active');
      document.getElementById(`tab-${button.dataset.tab}`)
        .classList.add('active');

      activeTab = button.dataset.tab;
      configureLivePolling();

      if (['performance', 'hardware'].includes(activeTab)) {
        loadTelemetry();
      }
    });
  });

  document.getElementById('scanBtn').addEventListener('click', async () => {
    await post('/api/scan', {});
    output.textContent =
      'Discovery scan started. Refreshing device list…';
    setTimeout(refreshDevices, 2500);
    setTimeout(refreshDevices, 7000);
  });

  document.getElementById('refreshAgentBtn')
    .addEventListener('click', async () => {
      status.textContent = 'Refreshing agent telemetry…';
      await details(true);
      status.textContent = 'Agent telemetry refreshed.';
    });

  document.getElementById('pingBtn').addEventListener('click', async () => {
    const device = selected();
    if (!device) return;
    const payload = await post('/api/ping', {target: device.ip});
    output.textContent =
      payload.stdout || payload.stderr || 'No output.';
  });

  document.getElementById('portsBtn').addEventListener('click', async () => {
    const device = selected();
    if (!device) return;
    const payload = await post('/api/ports', {target: device.ip});
    output.textContent =
      payload.stdout || payload.stderr || 'No output.';
  });

  async function runIperf(reverse) {
    const device = selected();
    if (!device) return;

    const payload = await post('/api/iperf', {
      target: device.ip,
      reverse,
    });

    output.textContent =
      `Direction: ${payload.direction}\n` +
      `Throughput: ${rate(payload.bits_per_second)}\n` +
      `Retransmits: ${payload.retransmits ?? 'n/a'}\n\n` +
      `${payload.stdout || payload.stderr || ''}`;

    await details(false);
  }

  document.getElementById('iperfBtn')
    .addEventListener('click', () => runIperf(false));
  document.getElementById('iperfReverseBtn')
    .addEventListener('click', () => runIperf(true));

  document.getElementById('resultsBtn')
    .addEventListener('click', async () => {
      const device = selected();
      const response = await fetch(
        `/api/results?target=${encodeURIComponent(device?.ip || '')}`
      );
      const payload = await response.json();

      output.textContent = payload.results.length
        ? payload.results.map(result =>
            `${result.created_at} | ${result.target_ip} | ` +
            `${result.direction} | ${rate(result.bits_per_second)} | ` +
            `retransmits ${result.retransmits ?? 'n/a'}`
          ).join('\n')
        : 'No saved iperf3 results for this device.';

      status.textContent = 'Saved results loaded.';
    });

  select.addEventListener('change', async () => {
    await details(false);
    configureLivePolling();
  });

  window.addEventListener('resize', drawTelemetryChart);
  document.addEventListener('visibilitychange', configureLivePolling);

  refreshDevices();
  setInterval(refreshDevices, 15000);
