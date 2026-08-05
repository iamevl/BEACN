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
    await loadDocker(false);

    const resolved = current.device || device;
    document.getElementById('refreshAgentBtn').disabled =
      !resolved.agent_available;

    document.getElementById('iperfBtn').disabled =
      !resolved.iperf_available;

    document.getElementById('iperfReverseBtn').disabled =
      !resolved.iperf_available;

    document.getElementById('editIdentityBtn').disabled =
      !resolved;
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

  function visibleDevices() {
    if (
      !activeDeviceTypeFilter ||
      !Array.isArray(activeDeviceTypeFilter.types) ||
      !activeDeviceTypeFilter.types.length
    ) {
      return devices;
    }

    const allowedTypes = new Set(
      activeDeviceTypeFilter.types
    );

    return devices.filter(device =>
      allowedTypes.has(
        device.device_type || 'unknown'
      )
    );
  }


  function deviceInventoryLabel(device) {
    const hostname = String(
      device.hostname || ''
    ).toLowerCase();

    const vendor = String(
      device.vendor || ''
    ).toLowerCase();

    const deviceType =
      device.device_type || 'unknown';

    if (deviceType === 'computer') {
      return 'PC';
    }

    if (
      deviceType === 'media_tuner' ||
      hostname.startsWith('hdhr-') ||
      vendor.includes('silicondust')
    ) {
      return 'HDHomeRun TV tuner';
    }

    if (
      deviceType === 'speaker' &&
      vendor.includes('apple') &&
      (
        hostname.includes('pod') ||
        hostname.includes('homepod')
      )
    ) {
      return 'Apple HomePod';
    }

    if (
      deviceType === 'appliance' &&
      vendor.includes('dyson')
    ) {
      return 'Dyson fan';
    }

    if (
      deviceType === 'access_point' &&
      hostname.includes('deco')
    ) {
      return 'Wi-Fi repeater';
    }

    if (
      deviceType === 'router' &&
      (
        vendor.includes('asus') ||
        hostname.startsWith('rt-')
      )
    ) {
      return 'ASUS router';
    }

    const presentation =
      typeof deviceTypeDetails === 'function'
        ? deviceTypeDetails(deviceType)
        : null;

    return presentation?.label || String(deviceType)
      .replaceAll('_', ' ')
      .replace(/\b\w/g, letter => letter.toUpperCase());
  }


  function renderDeviceOptions(preferredValue = '') {
    const filteredDevices = visibleDevices();

    select.innerHTML = filteredDevices.map(device => `
      <option value="${esc(device.ip)}">
        ${esc(
          `${deviceTypeDetails(
            device.device_type || 'unknown'
          ).icon} ` +
          (
            device.display_name
              ? `${device.display_name} · `
              : (
                  device.hostname
                    ? `${device.hostname} · `
                    : ''
                )
          ) +
          device.ip +
          ` · ${deviceInventoryLabel(device)}` +
          (device.agent_available ? ' · Agent' : '') +
          (device.iperf_available ? ' · iperf3' : '')
        )}
      </option>`).join('');

    if (
      preferredValue &&
      filteredDevices.some(
        device => device.ip === preferredValue
      )
    ) {
      select.value = preferredValue;
    } else if (filteredDevices.length) {
      select.value = filteredDevices[0].ip;
    }

    select.disabled = filteredDevices.length === 0;

    if (!filteredDevices.length) {
      select.innerHTML = `
        <option value="">
          No devices match this classification
        </option>
      `;
    }
  }


  async function applyDeviceTypeFilter(filter) {
    const previousValue = select.value;

    activeDeviceTypeFilter = filter;
    renderDeviceOptions(previousValue);

    await details(false);
  }


  async function refreshDevices() {
    const response = await fetch('/api/devices');
    const payload = await response.json();
    const oldValue = select.value;

    devices = payload.devices;
    renderDeviceOptions(oldValue);

    document.getElementById('onlineCount').textContent =
      devices.filter(device => device.is_online).length;
    document.getElementById('agentCount').textContent =
      devices.filter(device => device.agent_available).length;
    document.getElementById('iperfCount').textContent =
      devices.filter(device => device.iperf_available).length;
    document.getElementById('lastRefresh').textContent =
      new Date().toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit'
      });

    status.textContent = payload.scan.running
      ? 'Network scan running…'
      : (payload.scan.last_error || '');

    await details(false);
  }

