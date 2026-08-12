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

    const connection =
        device.connection_source === 'manual'
            ? ` · ${device.connection_method}`
            : '';

    return (
        `${presentation.label} · ${device.ip}${connection}`
    );
}


function topologyDeviceButton(device, extraClass = '') {
    const presentation = deviceTypeDetails(
        device.device_type || 'unknown'
    );

    const stateClass = device.is_online
        ? 'online'
        : 'offline';

    const ip = String(
        device.ip || ''
    ).trim();

    const mac = String(
        device.mac ||
        device.primary_mac ||
        ''
    ).trim();

    return `
        <button
          type="button"
          class="
            topology-node
            topology-device-node
            ${stateClass}
            ${extraClass}
          "
          data-topology-ip="${esc(ip)}"
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

            <small class="topology-device-type-label">
              ${esc(presentation.label)}
            </small>

            <span class="topology-addresses">
              ${
                  ip
                      ? `
                          <span class="topology-address-row">
                            <span class="topology-address-label">
                              IP
                            </span>

                            <span class="topology-address-value">
                              ${esc(ip)}
                            </span>
                          </span>
                        `
                      : ''
              }

              <span
                class="
                  topology-address-row
                  ${mac ? '' : 'topology-address-row-muted'}
                "
              >
                <span class="topology-address-label">
                  MAC
                </span>

                <span class="topology-address-value">
                  ${esc(mac || 'Not discovered')}
                </span>
              </span>
            </span>
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


function topologySortDevices(items) {
    return [...items].sort((left, right) => {
        const onlineDifference =
            Number(right.is_online) -
            Number(left.is_online);

        if (onlineDifference) {
            return onlineDifference;
        }

        return topologyDeviceName(left).localeCompare(
            topologyDeviceName(right)
        );
    });
}


function topologyConnectionMethod(device) {
    const method = String(
        device.connection_method || ''
    ).toLowerCase();

    if (method === 'wired' || method === 'wireless') {
        return method;
    }

    return null;
}


function topologyCoreServiceSubtitle(device) {
    const method = topologyConnectionMethod(device);

    return [
        'DNS service',
        device.ip,
        method
    ]
        .filter(Boolean)
        .join(' · ');
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

    const focusedColumns = model.columns
        .map(column => {
            if (column.kind === 'access_point') {
                const rootMatches =
                    topologyDeviceMatchesFocus(
                        column.device,
                        focus
                    );

                const matchingClients =
                    column.clients.filter(device =>
                        topologyDeviceMatchesFocus(
                            device,
                            focus
                        )
                    );

                if (
                    !rootMatches &&
                    !matchingClients.length
                ) {
                    return null;
                }

                return {
                    ...column,
                    clients: rootMatches
                        ? []
                        : matchingClients,
                    focusedRoot: rootMatches
                };
            }

            const infrastructure =
                column.infrastructure
                    .map(branch => {
                        const rootMatches =
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

                        if (
                            !rootMatches &&
                            !matchingClients.length
                        ) {
                            return null;
                        }

                        return {
                            ...branch,
                            clients: rootMatches
                                ? []
                                : matchingClients,
                            focusedRoot: rootMatches
                        };
                    })
                    .filter(Boolean);

            const direct =
                column.direct.filter(device =>
                    topologyDeviceMatchesFocus(
                        device,
                        focus
                    )
                );

            if (
                !infrastructure.length &&
                !direct.length
            ) {
                return null;
            }

            return {
                ...column,
                infrastructure,
                direct
            };
        })
        .filter(Boolean);

    const coreServices =
        model.coreServices.filter(device =>
            topologyDeviceMatchesFocus(
                device,
                focus
            )
        );

    const unassigned =
        model.unassigned.filter(device =>
            topologyDeviceMatchesFocus(
                device,
                focus
            )
        );

    const matchingDevices =
        devices.filter(device =>
            topologyDeviceMatchesFocus(
                device,
                focus
            )
        );

    return {
        ...model,
        columns: focusedColumns,
        coreServices,
        unassigned,
        focused: true,
        focus,
        focusCount: matchingDevices.length
    };
}


function topologyClientList(clients) {
    if (!clients.length) {
        return `
            <div class="topology-empty-branch">
              No connected devices assigned.
            </div>
        `;
    }

    return clients
        .map(device => topologyDeviceButton(device))
        .join('');
}


function renderInfrastructureColumn(branch) {
    const childInfrastructure =
        Array.isArray(branch.infrastructure)
            ? branch.infrastructure
            : [];

    const clients =
        Array.isArray(branch.clients)
            ? branch.clients
            : [];

    const hasChildren =
        childInfrastructure.length ||
        clients.length;

    return `
        <section class="topology-infrastructure-column">
          <div class="topology-infrastructure-root">
            ${topologyDeviceButton(
                branch.device,
                'topology-infrastructure-node'
            )}
          </div>

          ${
              hasChildren
                  ? `
                      <div
                        class="topology-infrastructure-line"
                        aria-hidden="true"
                      ></div>
                    `
                  : ''
          }

          ${
              branch.focusedRoot
                  ? `
                      <div class="topology-path-end">
                        Selected infrastructure node
                      </div>
                    `
                  : `
                      ${
                          clients.length
                              ? `
                                  <div class="topology-client-stack">
                                    ${topologyClientList(
                                        topologySortDevices(
                                            clients
                                        )
                                    )}
                                  </div>
                                `
                              : ''
                      }

                      ${
                          childInfrastructure.length
                              ? `
                                  <div
                                    class="topology-nested-infrastructure"
                                  >
                                    ${childInfrastructure
                                        .map(
                                            renderInfrastructureColumn
                                        )
                                        .join('')}
                                  </div>
                                `
                              : ''
                      }

                      ${
                          !clients.length &&
                          !childInfrastructure.length
                              ? `
                                  <div
                                    class="topology-empty-branch"
                                  >
                                    No connected devices assigned.
                                  </div>
                                `
                              : ''
                      }
                    `
          }
        </section>
    `;
}

function renderDirectClientGroup(devices, _label) {
    if (!devices.length) {
        return '';
    }

    return `
        <section class="topology-direct-router-clients">
          <div class="topology-client-stack">
            ${topologyClientList(devices)}
          </div>
        </section>
    `;
}


function renderAccessPointColumn(column) {
    return `
        <section
          class="
            topology-route-column
            topology-route-column-access-point
          "
          data-topology-ap-ip="${esc(
              column.device.ip
          )}"
          data-topology-parent-ip="${esc(
              column.parentIp || ''
          )}"
          style="
            --topology-column-colour:
            ${column.colour}
          "
        >
          <div class="topology-route-column-root">
            ${topologyDeviceButton(
                column.device,
                'topology-infrastructure-node'
            )}
          </div>

          <div class="topology-column-client-line"></div>

          <div class="topology-client-stack">
            ${
                column.focusedRoot
                    ? `
                        <div class="topology-path-end">
                          Selected access point
                        </div>
                      `
                    : topologyClientList(
                        column.clients
                    )
            }
          </div>
        </section>
    `;
}


function renderWiredColumn(column) {
    return `
        <section
          class="
            topology-route-column
            topology-route-column-wired
          "
          style="
            --topology-column-colour:
            ${column.colour}
          "
        >
          <div class="topology-route-column-header">
            <span aria-hidden="true">
              ${column.icon}
            </span>

            <div>
              <strong>${column.label}</strong>

              ${
                  column.relationshipResolved === false
                      ? `
                          <small>
                            Parent relationship not yet resolved
                          </small>
                        `
                      : ''
              }

              ${
                  column.relationshipResolved === false
                      ? `
                          <small>
                            Parent relationship not yet resolved
                          </small>
                        `
                      : ''
              }
              <small>Router wired connections</small>
            </div>
          </div>

          <div class="topology-column-client-line"></div>

          <div class="topology-column-body">
            ${
                column.infrastructure
                    .map(renderInfrastructureColumn)
                    .join('')
            }

            ${renderDirectClientGroup(
                column.direct,
                'Direct to router'
            )}

            ${
                !column.infrastructure.length &&
                !column.direct.length
                    ? `
                        <div class="topology-empty-branch">
                          No mapped wired connections.
                        </div>
                      `
                    : ''
            }
          </div>
        </section>
    `;
}


function renderWirelessColumn(column) {
    return `
        <section
          class="
            topology-route-column
            topology-route-column-wireless
          "
          style="
            --topology-column-colour:
            ${column.colour}
          "
        >
          <div class="topology-route-column-header">
            <span aria-hidden="true">
              ${column.icon}
            </span>

            <div>
              <strong>${column.label}</strong>
              <small>Direct router Wi-Fi</small>
            </div>
          </div>

          <div class="topology-column-client-line"></div>

          <div class="topology-column-body">
            ${renderDirectClientGroup(
                column.direct,
                'Direct wireless clients'
            )}

            ${
                !column.direct.length
                    ? `
                        <div class="topology-empty-branch">
                          No devices mapped directly to router Wi-Fi.
                        </div>
                      `
                    : ''
            }
          </div>
        </section>
    `;
}


function renderTopologyColumn(column) {
    if (column.kind === 'access_point') {
        return renderAccessPointColumn(column);
    }

    if (column.kind === 'wired') {
        return renderWiredColumn(column);
    }

    return renderWirelessColumn(column);
}


function renderCoreServices(model) {
    if (!model.coreServices.length) {
        return '';
    }

    return `
        <section class="topology-core-services">
          <div class="topology-core-services-heading">
            <span aria-hidden="true">🧭</span>

            <div>
              <strong>Core network services</strong>

              <small>
                Logical services connected directly to
                the router, not an inline traffic path.
              </small>
            </div>
          </div>

          <div class="topology-core-services-grid">
            ${model.coreServices.map(device => {
                const presentation =
                    deviceTypeDetails(
                        device.device_type ||
                        'unknown'
                    );

                const stateClass =
                    device.is_online
                        ? 'online'
                        : 'offline';

                return `
                    <button
                      type="button"
                      class="
                        topology-node
                        topology-device-node
                        topology-core-service-node
                        ${stateClass}
                      "
                      data-topology-ip="${esc(device.ip)}"
                      style="
                        --topology-node-colour:
                        ${presentation.colour}
                      "
                      title="${esc(
                          topologyCoreServiceSubtitle(
                              device
                          )
                      )}"
                    >
                      <span
                        class="topology-node-icon"
                        aria-hidden="true"
                      >
                        🛡️
                      </span>

                      <span class="topology-node-copy">
                        <strong>
                          ${esc(
                              topologyDeviceName(device)
                          )}
                        </strong>

                        <small>
                          ${esc(
                              topologyCoreServiceSubtitle(
                                  device
                              )
                          )}
                        </small>
                      </span>

                      <span
                        class="topology-node-status"
                        aria-label="${
                            device.is_online
                                ? 'Online'
                                : 'Offline'
                        }"
                      ></span>
                    </button>
                `;
            }).join('')}
          </div>
        </section>
    `;
}


function renderTopologyBackbone(model) {
    const columns = model.columns || [];

    const cells = columns.map((column, index) => {
        const nextColumn = columns[index + 1];

        const nextIsWirelessChild =
            Boolean(
                nextColumn?.wirelessBackhaul &&
                nextColumn?.parentIp &&
                column?.device?.ip ===
                    nextColumn.parentIp
            );

        const currentIsWirelessChild =
            Boolean(column.wirelessBackhaul);

        const segmentClass = nextIsWirelessChild
            ? 'topology-backbone-segment-backhaul'
            : 'topology-backbone-segment-wired';

        const stemClass = currentIsWirelessChild
            ? 'topology-backbone-stem-backhaul'
            : 'topology-backbone-stem-wired';

        return `
            <div
              class="
                topology-backbone-cell
                ${stemClass}
              "
            >
              ${
                  index < columns.length - 1
                      ? `
                          <span
                            class="
                              topology-backbone-segment
                              ${segmentClass}
                            "
                            aria-hidden="true"
                          ></span>
                        `
                      : ''
              }

              ${
                  nextIsWirelessChild
                      ? `
                          <span
                            class="topology-backbone-label"
                          >
                            Wireless link
                            <small>(backhaul)</small>
                          </span>
                        `
                      : ''
              }
            </div>
        `;
    }).join('');

    return `
        <div class="topology-structural-backbone">
          <div class="topology-backbone-router-stem"></div>

          <div
            class="topology-backbone-grid"
            style="
              --topology-column-count:
              ${Math.max(1, columns.length)}
            "
          >
            ${cells}
          </div>
        </div>
    `;
}


function renderUnassignedPanel(model) {
    if (!model.unassigned.length) {
        return `
            <section class="topology-unassigned topology-unassigned-empty">
              <div class="topology-unassigned-header">
                <div>
                  <h3>Unassigned devices</h3>

                  <p>
                    Every discovered endpoint has been mapped
                    into the topology.
                  </p>
                </div>

                <span class="badge">0 devices</span>
              </div>
            </section>
        `;
    }

    return `
        <section class="topology-unassigned">
          <div class="topology-unassigned-header">
            <div>
              <h3>Unassigned devices</h3>

              <p>
                These devices are known to BEACN but have not
                been assigned a connection and parent device.
                Use Edit identity to place them into the map.
              </p>
            </div>

            <span class="badge">
              ${model.unassigned.length}
              device${
                  model.unassigned.length === 1
                      ? ''
                      : 's'
              }
            </span>
          </div>

          <div class="topology-unassigned-grid">
            ${
                model.unassigned
                    .map(device =>
                        topologyDeviceButton(
                            device,
                            'topology-unassigned-node'
                        )
                    )
                    .join('')
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
        .querySelectorAll(
            '.topology-node.selected'
        )
        .forEach(node =>
            node.classList.remove('selected')
        );

    document
        .querySelector(
            `[data-topology-ip="${CSS.escape(ip)}"]`
        )
        ?.classList.add('selected');
}


function topologyUpstreamInfrastructureLabel(node) {
    const type =
        node?.device?.infrastructure_type;

    const labels = {
        isp_gateway: 'ISP Gateway',
        router: 'Router',
        firewall: 'Firewall',
        switch: 'Network switch',
        internet: 'Internet'
    };

    return (
        labels[type] ||
        deviceTypeDetails(
            node?.device?.device_type ||
            'unknown'
        ).label
    );
}


function topologyUpstreamInfrastructureIcon(node) {
    const type =
        node?.device?.infrastructure_type;

    const icons = {
        isp_gateway: '🌐',
        firewall: '🛡️',
        switch: '🔀',
        router: '🛜'
    };

    return (
        icons[type] ||
        deviceTypeDetails(
            node?.device?.device_type ||
            'unknown'
        ).icon
    );
}


function renderTopologyUpstreamNode(
    node,
    primaryRouter
) {
    const device = node?.device;

    if (!device) {
        return '';
    }

    const isInternet =
        device.infrastructure_type ===
            'internet';

    if (isInternet) {
        return `
            <div class="topology-internet-row">
              <div class="topology-internet-node">
                <span aria-hidden="true">🌍</span>
                <strong>
                  ${esc(
                      device.display_name ||
                      'Internet'
                  )}
                </strong>
              </div>
            </div>
        `;
    }

    const isPrimaryRouter =
        primaryRouter &&
        (
            node.ref ===
                `device:${primaryRouter.ip}` ||
            device.ip === primaryRouter.ip
        );

    if (isPrimaryRouter) {
        return `
            <div class="topology-router-row">
              ${topologyDeviceButton(
                  primaryRouter,
                  'topology-router-node'
              )}
            </div>
        `;
    }

    const ip =
        String(device.ip || '').trim();

    const makeModel = [
        device.manufacturer,
        device.model
    ]
        .filter(Boolean)
        .join(' ');

    return `
        <div class="topology-upstream-object-row">
          <div
            class="
              topology-node
              topology-device-node
              topology-upstream-object
              online
            "
          >
            <span
              class="topology-node-icon"
              aria-hidden="true"
            >
              ${topologyUpstreamInfrastructureIcon(
                  node
              )}
            </span>

            <span class="topology-node-copy">
              <strong>
                ${esc(
                    topologyDeviceName(device)
                )}
              </strong>

              <small
                class="topology-device-type-label"
              >
                ${esc(
                    topologyUpstreamInfrastructureLabel(
                        node
                    )
                )}
              </small>

              ${
                  makeModel
                      ? `
                          <small>
                            ${esc(makeModel)}
                          </small>
                        `
                      : ''
              }

              ${
                  ip
                      ? `
                          <span class="topology-addresses">
                            <span
                              class="topology-address-row"
                            >
                              <span
                                class="topology-address-label"
                              >
                                IP
                              </span>

                              <span
                                class="topology-address-value"
                              >
                                ${esc(ip)}
                              </span>
                            </span>
                          </span>
                        `
                      : ''
              }
            </span>
          </div>
        </div>
    `;
}


function renderTopologyUpstream(model) {
    const path =
        Array.isArray(model.upstreamPath)
            ? model.upstreamPath
            : [];

    if (!path.length) {
        return `
            <div class="topology-internet-row">
              <div class="topology-internet-node">
                <span aria-hidden="true">🌍</span>
                <strong>Internet</strong>
              </div>
            </div>

            <div class="topology-trunk-line"></div>

            <div class="topology-router-row">
              ${topologyDeviceButton(
                  model.primaryRouter,
                  'topology-router-node'
              )}
            </div>
        `;
    }

    return `
        <div class="topology-upstream-chain">
          ${path.map((node, index) => `
              ${renderTopologyUpstreamNode(
                  node,
                  model.primaryRouter
              )}

              ${
                  index < path.length - 1
                      ? `
                          <div
                            class="topology-upstream-link"
                            aria-hidden="true"
                          ></div>
                        `
                      : ''
              }
          `).join('')}
        </div>
    `;
}


function refreshTopology() {
    const stage =
        document.getElementById('topologyStage');

    const countBadge =
        document.getElementById(
            'topologyDeviceCount'
        );

    if (!stage) {
        return;
    }

    const fullModel = buildTopologyModel();
    const model = focusTopologyModel(fullModel);

    if (countBadge) {
        countBadge.textContent = model.focused
            ? (
                `${model.focusCount} ` +
                `${model.focus.label} · critical path`
              )
            : (
                `${model.total} devices · ` +
                `${model.online} online`
              );
    }

    const router = model.primaryRouter.synthetic
        ? `
            <div
              class="
                topology-node
                topology-router-node
                online
              "
              style="
                --topology-node-colour:#22d3ee
              "
            >
              <span class="topology-node-icon">
                🛜
              </span>

              <span class="topology-node-copy">
                <strong>Network gateway</strong>

                <small>
                  Router not yet identified
                </small>
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
                          ${esc(model.focus.label)}
                          critical paths
                        </strong>

                        <span>
                          Showing only mapped routes for
                          matching devices.
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

        <div
          class="
            topology-map
            topology-map-transport
            ${model.focused
                ? 'topology-map-focused'
                : ''}
          "
        >
          ${renderTopologyUpstream(model)}

          ${renderCoreServices(model)}

          ${renderTopologyBackbone(model)}

          <div
            class="topology-route-column-grid"
            style="
              --topology-column-count:
              ${Math.max(1, model.columns.length)}
            "
          >
            ${
                model.columns
                    .map(renderTopologyColumn)
                    .join('')
            }
          </div>

          ${renderUnassignedPanel(model)}
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
