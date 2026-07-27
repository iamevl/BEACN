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

      if (activeTab === 'docker') {
        loadDocker(true);
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
