let deviceTypeChartData = [];
let deviceTypeChartSegments = [];
let hoveredDeviceType = null;


const deviceTypePresentation = {
    access_point: {
        label: 'Access point',
        icon: '📡',
        colour: '#38bdf8'
    },
    appliance: {
        label: 'Appliance',
        icon: '🌀',
        colour: '#f97316'
    },
    camera: {
        label: 'Camera',
        icon: '📷',
        colour: '#c084fc'
    },
    computer: {
        label: 'PC',
        icon: '💻',
        colour: '#34d399'
    },
    doorbell: {
        label: 'Doorbell',
        icon: '🔔',
        colour: '#fbbf24'
    },
    game_console: {
        label: 'Game console',
        icon: '🎮',
        colour: '#a78bfa'
    },
    iot: {
        label: 'IoT',
        icon: '🔌',
        colour: '#60a5fa'
    },
    media_tuner: {
        label: 'TV tuner',
        icon: '📺',
        colour: '#818cf8'
    },
    nas: {
        label: 'NAS',
        icon: '💾',
        colour: '#fb923c'
    },
    phone: {
        label: 'Phone',
        icon: '📱',
        colour: '#f472b6'
    },
    raspberry_pi: {
        label: 'Raspberry Pi',
        icon: '🍓',
        colour: '#e11d48'
    },
    router: {
        label: 'Router',
        icon: '🛜',
        colour: '#22d3ee'
    },
    speaker: {
        label: 'Speaker',
        icon: '🔊',
        colour: '#2dd4bf'
    },
    unknown: {
        label: 'Unknown',
        icon: '❓',
        colour: '#64748b'
    },
    ups: {
        label: 'UPS',
        icon: '🔋',
        colour: '#facc15'
    },
    other: {
        label: 'Other classified',
        icon: '•••',
        colour: '#94a3b8'
    }
};


function deviceTypeDetails(value) {
    const fallbackLabel = String(value || 'unknown')
        .replaceAll('_', ' ')
        .replace(/\b\w/g, letter => letter.toUpperCase());

    return deviceTypePresentation[value] || {
        label: fallbackLabel,
        icon: '◈',
        colour: '#94a3b8'
    };
}


function prepareDeviceTypeData(types, total) {
    const meaningful = types
        .map(item => ({
            device_type: item.device_type || 'unknown',
            total: Number(item.total) || 0,
            member_types: [
                item.device_type || 'unknown'
            ]
        }))
        .filter(item => item.total > 0)
        .sort((left, right) => right.total - left.total);

    const visible = meaningful.slice(0, 6);
    const remaining = meaningful.slice(6);

    const otherTotal = remaining.reduce(
        (sum, item) => sum + item.total,
        0
    );

    if (otherTotal > 0) {
        visible.push({
            device_type: 'other',
            total: otherTotal,
            member_types: remaining.map(
                item => item.device_type
            )
        });
    }

    return visible.map(item => ({
        ...item,
        percentage: total > 0
            ? (item.total / total) * 100
            : 0
    }));
}


function selectedDeviceTypeKey() {
    return activeDeviceTypeFilter?.key || null;
}


function deviceTypeIsSelected(item) {
    return selectedDeviceTypeKey() === item.device_type;
}


function drawDeviceTypeChart() {
    const canvas =
        document.getElementById('deviceTypeChart');

    if (!canvas) {
        return;
    }

    const rect = canvas.getBoundingClientRect();
    const ratio = window.devicePixelRatio || 1;

    canvas.width = Math.max(
        1,
        Math.floor(rect.width * ratio)
    );

    canvas.height = Math.max(
        1,
        Math.floor(rect.height * ratio)
    );

    const context = canvas.getContext('2d');

    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(
        0,
        0,
        rect.width,
        rect.height
    );

    const total = deviceTypeChartData.reduce(
        (sum, item) => sum + item.total,
        0
    );

    deviceTypeChartSegments = [];

    if (!total) {
        context.fillStyle = '#95a4c4';
        context.font = '14px system-ui';
        context.textAlign = 'center';

        context.fillText(
            'No classified devices',
            rect.width / 2,
            rect.height / 2
        );

        return;
    }

    const centreX = rect.width / 2;
    const centreY = rect.height / 2;
    const baseRadius = Math.max(
        20,
        Math.min(rect.width, rect.height) / 2 - 12
    );
    const innerRadius = baseRadius * 0.66;
    const selectedKey = selectedDeviceTypeKey();

    let startAngle = -Math.PI / 2;

    deviceTypeChartData.forEach(item => {
        const presentation =
            deviceTypeDetails(item.device_type);

        const sliceAngle =
            (item.total / total) * Math.PI * 2;

        const endAngle =
            startAngle + sliceAngle;

        const selected = deviceTypeIsSelected(item);
        const hovered =
            hoveredDeviceType === item.device_type;

        const radius =
            baseRadius + (selected || hovered ? 6 : 0);

        const inactive =
            selectedKey && !selected;

        context.save();
        context.globalAlpha = inactive ? 0.32 : 1;

        context.beginPath();

        context.arc(
            centreX,
            centreY,
            radius,
            startAngle,
            endAngle
        );

        context.arc(
            centreX,
            centreY,
            innerRadius,
            endAngle,
            startAngle,
            true
        );

        context.closePath();
        context.fillStyle = presentation.colour;
        context.fill();

        if (selected) {
            context.strokeStyle = '#f8fafc';
            context.lineWidth = 2;
            context.stroke();
        }

        context.restore();

        deviceTypeChartSegments.push({
            item,
            startAngle,
            endAngle,
            innerRadius,
            outerRadius: radius,
            centreX,
            centreY
        });

        startAngle = endAngle;
    });
}


function renderDeviceTypeLegend() {
    const legend =
        document.getElementById('deviceTypeLegend');

    if (!legend) {
        return;
    }

    if (!deviceTypeChartData.length) {
        legend.innerHTML = `
            <div class="device-type-empty">
                No device classifications are available.
            </div>
        `;
        return;
    }

    legend.innerHTML = deviceTypeChartData
        .map(item => {
            const presentation =
                deviceTypeDetails(item.device_type);

            const selected =
                deviceTypeIsSelected(item);

            return `
                <button
                  type="button"
                  class="
                    device-type-legend-row
                    ${selected ? 'active' : ''}
                  "
                  data-device-type="${esc(item.device_type)}"
                  aria-pressed="${selected ? 'true' : 'false'}"
                >
                    <span
                      class="device-type-legend-swatch"
                      style="background:${presentation.colour}"
                      aria-hidden="true"
                    ></span>

                    <span
                      class="device-type-legend-icon"
                      aria-hidden="true"
                    >
                        ${presentation.icon}
                    </span>

                    <span class="device-type-legend-label">
                        ${esc(presentation.label)}
                    </span>

                    <span class="device-type-legend-value">
                        <strong>${item.total}</strong>
                        <small>
                            ${Math.round(item.percentage)}%
                        </small>
                    </span>
                </button>
            `;
        })
        .join('');

    legend
        .querySelectorAll('[data-device-type]')
        .forEach(button => {
            button.addEventListener(
                'click',
                () => {
                    toggleDeviceTypeFilter(
                        button.dataset.deviceType
                    );
                }
            );

            button.addEventListener(
                'mouseenter',
                () => {
                    hoveredDeviceType =
                        button.dataset.deviceType;

                    drawDeviceTypeChart();
                }
            );

            button.addEventListener(
                'mouseleave',
                () => {
                    hoveredDeviceType = null;
                    drawDeviceTypeChart();
                }
            );
        });
}


async function toggleDeviceTypeFilter(deviceType) {
    const item = deviceTypeChartData.find(
        candidate =>
            candidate.device_type === deviceType
    );

    if (!item) {
        return;
    }

    const alreadySelected =
        selectedDeviceTypeKey() === deviceType;

    const filter = alreadySelected
        ? null
        : {
            key: item.device_type,
            label: deviceTypeDetails(
                item.device_type
            ).label,
            types: item.member_types
        };

    await applyDeviceTypeFilter(filter);

    renderDeviceTypeLegend();
    drawDeviceTypeChart();

    const badge =
        document.getElementById(
            'deviceTypeTotalBadge'
        );

    if (badge) {
        badge.textContent = filter
            ? `${item.total} ${filter.label}`
            : `${devices.length} devices`;
    }
}


function normaliseChartAngle(angle) {
    const fullCircle = Math.PI * 2;
    let result = angle;

    while (result < -Math.PI / 2) {
        result += fullCircle;
    }

    while (result >= Math.PI * 1.5) {
        result -= fullCircle;
    }

    return result;
}


function segmentAtCanvasPoint(canvas, event) {
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;

    return deviceTypeChartSegments.find(segment => {
        const dx = x - segment.centreX;
        const dy = y - segment.centreY;
        const distance = Math.sqrt(
            dx * dx + dy * dy
        );

        if (
            distance < segment.innerRadius ||
            distance > segment.outerRadius
        ) {
            return false;
        }

        const angle = normaliseChartAngle(
            Math.atan2(dy, dx)
        );

        return (
            angle >= segment.startAngle &&
            angle < segment.endAngle
        );
    });
}


function bindDeviceTypeChartInteraction() {
    const canvas =
        document.getElementById('deviceTypeChart');

    if (
        !canvas ||
        canvas.dataset.interactionBound === '1'
    ) {
        return;
    }

    canvas.dataset.interactionBound = '1';

    canvas.addEventListener(
        'mousemove',
        event => {
            const segment =
                segmentAtCanvasPoint(canvas, event);

            hoveredDeviceType =
                segment?.item.device_type || null;

            canvas.style.cursor =
                segment ? 'pointer' : 'default';

            drawDeviceTypeChart();
        }
    );

    canvas.addEventListener(
        'mouseleave',
        () => {
            hoveredDeviceType = null;
            canvas.style.cursor = 'default';
            drawDeviceTypeChart();
        }
    );

    canvas.addEventListener(
        'click',
        event => {
            const segment =
                segmentAtCanvasPoint(canvas, event);

            if (segment) {
                toggleDeviceTypeFilter(
                    segment.item.device_type
                );
            }
        }
    );
}


async function refreshDeviceTypes() {
    try {
        const response =
            await fetch('/api/device-types');

        if (!response.ok) {
            throw new Error(
                `Device type request failed: ${response.status}`
            );
        }

        const payload = await response.json();
        const total = Number(payload.total) || 0;

        deviceTypeChartData =
            prepareDeviceTypeData(
                Array.isArray(payload.types)
                    ? payload.types
                    : [],
                total
            );

        const totalElement =
            document.getElementById(
                'deviceTypeTotal'
            );

        const badge =
            document.getElementById(
                'deviceTypeTotalBadge'
            );

        if (totalElement) {
            totalElement.textContent = total;
        }

        if (badge) {
            badge.textContent =
                `${total} device${total === 1 ? '' : 's'}`;
        }

        bindDeviceTypeChartInteraction();
        drawDeviceTypeChart();
        renderDeviceTypeLegend();
    } catch (error) {
        console.error(error);

        const legend =
            document.getElementById(
                'deviceTypeLegend'
            );

        const badge =
            document.getElementById(
                'deviceTypeTotalBadge'
            );

        if (legend) {
            legend.innerHTML = `
                <div class="device-type-empty">
                    Unable to load device classifications.
                </div>
            `;
        }

        if (badge) {
            badge.textContent = 'Unavailable';
        }
    }
}


window.addEventListener(
    'resize',
    drawDeviceTypeChart
);

refreshDeviceTypes();
