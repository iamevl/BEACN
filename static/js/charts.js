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

