const TOPOLOGY_WIRED_TYPES = new Set([
    'computer',
    'nas',
    'raspberry_pi',
    'media_tuner',
    'ups',
    'switch'
]);


const TOPOLOGY_WIRELESS_TYPES = new Set([
    'access_point',
    'appliance',
    'camera',
    'doorbell',
    'game_console',
    'iot',
    'phone',
    'speaker',
    'television'
]);


function topologyDeviceName(device) {
    return (
        device.display_name ||
        device.hostname ||
        device.ip ||
        'Unknown device'
    );
}


function topologyDeviceSubtitle(device) {
    const presentation = deviceTypeDetails(
        device.device_type || 'unknown'
    );

    return `${presentation.label} · ${device.ip}`;
}


function topologyDeviceButton(device, extraClass = '') {
    const presentation = deviceTypeDetails(
        device.device_type || 'unknown'
    );

    const stateClass = device.is_online
        ? 'online'
        : 'offline';

    return `
        <button
          type="button"
          class="
            topology-node
            topology-device-node
            ${stateClass}
            ${extraClass}
          "
          data-topology-ip="${esc(device.ip)}"
          style="
            --topology-node-colour:
            ${presentation.colour}
          "
          title="${esc(topologyDeviceSubtitle(device))}"
        >
          <span
            class="topology-node-icon"
            aria-hidden="true"
          >
            ${presentation.icon}
          </span>

          <span class="topology-node-copy">
            <strong>
              ${esc(topologyDeviceName(device))}
            </strong>

            <small>
              ${esc(topologyDeviceSubtitle(device))}
            </small>
          </span>

          <span
            class="topology-node-status"
            aria-label="${device.is_online ? 'Online' : 'Offline'}"
          ></span>
        </button>
    `;
}


function topologySyntheticRouter() {
    return {
        ip: '',
        hostname: 'Network gateway',
        display_name: 'Network gateway',
        device_type: 'router',
        is_online: 1,
        synthetic: true
    };
}


function buildTopologyModel() {
    const inventory = Array.isArray(devices)
        ? devices
        : [];

    const routers = inventory.filter(
        device => device.device_type === 'router'
    );

    const switches = inventory.filter(
        device => device.device_type === 'switch'
    );

    const accessPoints = inventory.filter(
        device => device.device_type === 'access_point'
    );

    const primaryRouter =
        routers[0] || topologySyntheticRouter();

    const infrastructureIps = new Set(
        [
            ...routers,
            ...switches,
            ...accessPoints
        ].map(device => device.ip)
    );

    const clients = inventory.filter(
        device => !infrastructureIps.has(device.ip)
    );

    const branches = [];

    switches.forEach(device => {
        branches.push({
            device,
            role: 'wired',
            clients: []
        });
    });

    accessPoints.forEach(device => {
        branches.push({
            device,
            role: 'wireless',
            clients: []
        });
    });

    branches.push({
        device: primaryRouter,
        role: 'router',
        clients: []
    });

    const switchBranches = branches.filter(
        branch => branch.role === 'wired'
    );

    const accessPointBranches = branches.filter(
        branch => branch.role === 'wireless'
    );

    const routerBranch = branches.find(
        branch => branch.role === 'router'
    );

    let wiredIndex = 0;
    let wirelessIndex = 0;

    clients.forEach(device => {
        const type = device.device_type || 'unknown';

        if (
            TOPOLOGY_WIRED_TYPES.has(type) &&
            switchBranches.length
        ) {
            switchBranches[
                wiredIndex % switchBranches.length
            ].clients.push(device);

            wiredIndex += 1;
            return;
        }

        if (
            TOPOLOGY_WIRELESS_TYPES.has(type) &&
            accessPointBranches.length
        ) {
            accessPointBranches[
                wirelessIndex % accessPointBranches.length
            ].clients.push(device);

            wirelessIndex += 1;
            return;
        }

        routerBranch.clients.push(device);
    });

    return {
        total: inventory.length,
        online: inventory.filter(
            device => device.is_online
        ).length,
        primaryRouter,
        branches
    };
}


function topologyFocus() {
    if (
        !activeDeviceTypeFilter ||
        !Array.isArray(activeDeviceTypeFilter.types) ||
        !activeDeviceTypeFilter.types.length
    ) {
        return null;
    }

    return {
        key: activeDeviceTypeFilter.key,
        label: activeDeviceTypeFilter.label,
        types: new Set(activeDeviceTypeFilter.types)
    };
}


function topologyDeviceMatchesFocus(device, focus) {
    if (!focus || !device) {
        return false;
    }

    return focus.types.has(
        device.device_type || 'unknown'
    );
}


function focusTopologyModel(model) {
    const focus = topologyFocus();

    if (!focus) {
        return {
            ...model,
            focused: false,
            focus: null,
            focusCount: model.total
        };
    }

    const focusedBranches = [];

    model.branches.forEach(branch => {
        const branchMatches =
            topologyDeviceMatchesFocus(
                branch.device,
                focus
            );

        const matchingClients =
            branch.clients.filter(device =>
                topologyDeviceMatchesFocus(
                    device,
                    focus
                )
            );

        if (!branchMatches && !matchingClients.length) {
            return;
        }

        focusedBranches.push({
            ...branch,
            clients: branchMatches
                ? []
                : matchingClients,
            focusedRoot: branchMatches
        });
    });

    const matchingDevices = devices.filter(device =>
        topologyDeviceMatchesFocus(device, focus)
    );

    return {
        ...model,
        branches: focusedBranches,
        focused: true,
        focus,
        focusCount: matchingDevices.length
    };
}


function topologyBranchTitle(branch) {
    if (branch.focusedRoot) {
        return 'Selected infrastructure';
    }

    if (branch.role === 'wired') {
        return 'Wired critical path';
    }

    if (branch.role === 'wireless') {
        return 'Wireless critical path';
    }

    return 'Direct critical path';
}


function renderTopologyBranch(branch) {
    const clients = [...branch.clients].sort(
        (left, right) => {
            const onlineDifference =
                Number(right.is_online) -
                Number(left.is_online);

            if (onlineDifference) {
                return onlineDifference;
            }

            return topologyDeviceName(left).localeCompare(
                topologyDeviceName(right)
            );
        }
    );

    const branchNode = branch.device.synthetic
        ? `
            <div
              class="
                topology-node
                topology-device-node
                topology-synthetic-node
                online
              "
              style="--topology-node-colour:#22d3ee"
            >
              <span
                class="topology-node-icon"
                aria-hidden="true"
              >
                🛜
              </span>

              <span class="topology-node-copy">
                <strong>Network gateway</strong>
                <small>Router not yet identified</small>
              </span>
            </div>
          `
        : topologyDeviceButton(
            branch.device,
            'topology-infrastructure-node'
        );

    return `
        <section class="topology-branch">
          <div class="topology-branch-heading">
            <span>${esc(topologyBranchTitle(branch))}</span>
            <strong>${clients.length}</strong>
          </div>

          <div class="topology-branch-root">
            ${branchNode}
          </div>

          <div class="topology-branch-line"></div>

          <div class="topology-client-grid">
            ${
                clients.length
                    ? clients
                        .map(device =>
                            topologyDeviceButton(device)
                        )
                        .join('')
                    : branch.focusedRoot
                      ? `
                          <div class="topology-path-end">
                            Selected infrastructure node
                          </div>
                        `
                      : `
                          <div class="topology-empty-branch">
                            No devices assigned to this branch.
                          </div>
                        `
            }
          </div>
        </section>
    `;
}


function bindTopologyNodes() {
    document
        .querySelectorAll('[data-topology-ip]')
        .forEach(node => {
            node.addEventListener(
                'click',
                () => selectTopologyDevice(
                    node.dataset.topologyIp
                )
            );
        });
}


async function selectTopologyDevice(ip) {
    if (!ip) {
        return;
    }

    if (
        activeDeviceTypeFilter &&
        typeof applyDeviceTypeFilter === 'function'
    ) {
        await applyDeviceTypeFilter(null);
        renderDeviceTypeLegend();
        drawDeviceTypeChart();
        refreshTopology();
    }

    renderDeviceOptions(ip);

    if (
        !Array.from(select.options).some(
            option => option.value === ip
        )
    ) {
        return;
    }

    select.value = ip;

    select.dispatchEvent(
        new Event('change')
    );

    document
        .querySelector('.device-tools-card')
        ?.scrollIntoView({
            behavior: 'smooth',
            block: 'start'
        });

    document
        .querySelectorAll('.topology-node.selected')
        .forEach(node =>
            node.classList.remove('selected')
        );

    document
        .querySelector(
            `[data-topology-ip="${CSS.escape(ip)}"]`
        )
        ?.classList.add('selected');
}


function refreshTopology() {
    const stage =
        document.getElementById('topologyStage');

    const countBadge =
        document.getElementById('topologyDeviceCount');

    if (!stage) {
        return;
    }

    const fullModel = buildTopologyModel();
    const model = focusTopologyModel(fullModel);

    if (countBadge) {
        countBadge.textContent = model.focused
            ? `${model.focusCount} ${model.focus.label} · critical path`
            : `${model.total} devices · ${model.online} online`;
    }

    const router = model.primaryRouter.synthetic
        ? `
            <div
              class="topology-node topology-router-node online"
              style="--topology-node-colour:#22d3ee"
            >
              <span class="topology-node-icon">🛜</span>

              <span class="topology-node-copy">
                <strong>Network gateway</strong>
                <small>Router not yet identified</small>
              </span>
            </div>
          `
        : topologyDeviceButton(
            model.primaryRouter,
            'topology-router-node'
        );

    stage.innerHTML = `
        ${
            model.focused
                ? `
                    <div class="topology-focus-banner">
                      <div>
                        <strong>
                          ${esc(model.focus.label)} critical paths
                        </strong>

                        <span>
                          Showing only the inferred routes between
                          matching devices and the internet.
                        </span>
                      </div>

                      <button
                        type="button"
                        id="clearTopologyFocus"
                      >
                        Show full topology
                      </button>
                    </div>
                  `
                : ''
        }

        <div class="
          topology-map
          ${model.focused ? 'topology-map-focused' : ''}
        ">
          <div class="topology-internet-row">
            <div class="topology-internet-node">
              <span aria-hidden="true">🌍</span>
              <strong>Internet</strong>
            </div>
          </div>

          <div class="topology-trunk-line"></div>

          <div class="topology-router-row">
            ${router}
          </div>

          <div class="topology-trunk-line"></div>

          <div class="topology-branch-grid">
            ${
                model.branches
                    .map(renderTopologyBranch)
                    .join('')
            }
          </div>
        </div>
    `;

    bindTopologyNodes();

    document
        .getElementById('clearTopologyFocus')
        ?.addEventListener(
            'click',
            async () => {
                await applyDeviceTypeFilter(null);
                renderDeviceTypeLegend();
                drawDeviceTypeChart();
                refreshTopology();
            }
        );
}
