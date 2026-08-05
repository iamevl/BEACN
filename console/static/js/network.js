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

  const identityEditor =
    document.getElementById('identityEditor');

  const identityDisplayName =
    document.getElementById('identityDisplayName');

  const identityDeviceType =
    document.getElementById('identityDeviceType');

  const identityEditorTarget =
    document.getElementById('identityEditorTarget');

  const identitySourceBadge =
    document.getElementById('identitySourceBadge');

  const identityEditorMessage =
    document.getElementById('identityEditorMessage');


  function closeIdentityEditor() {
    identityEditor.hidden = true;
    identityEditorMessage.textContent = '';
  }


  function openIdentityEditor() {
    const device = selected();

    if (!device) {
      return;
    }

    identityDisplayName.value =
      device.display_name || '';

    identityDeviceType.value =
      device.device_type || 'unknown';

    identityEditorTarget.textContent =
      `${device.hostname || device.ip} · ${device.ip}`;

    const source =
      device.device_type_source || 'unknown';

    identitySourceBadge.textContent =
      source === 'manual'
        ? 'Manual'
        : source === 'agent'
          ? 'Agent'
          : source === 'classifier'
            ? 'Automatic'
            : 'Unknown';

    identityEditorMessage.textContent = '';
    identityEditor.hidden = false;

    identityEditor.scrollIntoView({
      behavior: 'smooth',
      block: 'nearest'
    });

    identityDisplayName.focus();
  }


  async function saveIdentity() {
    const device = selected();

    if (!device) {
      return;
    }

    const saveButton =
      document.getElementById('saveIdentityBtn');

    saveButton.disabled = true;
    identityEditorMessage.textContent =
      'Saving identity…';

    try {
      const response = await fetch(
        `/api/device/${encodeURIComponent(device.ip)}/identity`,
        {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json'
          },
          body: JSON.stringify({
            display_name:
              identityDisplayName.value.trim(),
            device_type:
              identityDeviceType.value
          })
        }
      );

      const payload = await response.json();

      if (!response.ok || !payload.ok) {
        throw new Error(
          payload.error || 'Unable to save identity.'
        );
      }

      identityEditorMessage.textContent =
        'Identity saved.';

      status.textContent =
        'Manual device identity saved.';

      await refreshDevices();
      await refreshDeviceTypes();

      setTimeout(closeIdentityEditor, 650);
    } catch (error) {
      identityEditorMessage.textContent =
        error.message;
    } finally {
      saveButton.disabled = false;
    }
  }


  document.getElementById('editIdentityBtn')
    .addEventListener('click', openIdentityEditor);

  document.getElementById('cancelIdentityBtn')
    .addEventListener('click', closeIdentityEditor);

  document.getElementById('saveIdentityBtn')
    .addEventListener('click', saveIdentity);

  identityDisplayName.addEventListener(
    'keydown',
    event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        saveIdentity();
      }

      if (event.key === 'Escape') {
        closeIdentityEditor();
      }
    }
  );


  select.addEventListener('change', async () => {
    closeIdentityEditor();
    await details(false);
    configureLivePolling();
  });

  window.addEventListener('resize', drawTelemetryChart);
  document.addEventListener('visibilitychange', configureLivePolling);

  async function refreshDashboard() {
    await refreshDevices();
    await refreshHealth();
  }

  refreshDashboard();
  setInterval(refreshDashboard, 5000);
