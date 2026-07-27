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

