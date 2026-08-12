"use strict";


function deviceProfileName(device) {
    return (
        device?.display_name ||
        device?.hostname ||
        device?.ip ||
        "Unknown device"
    );
}


function renderDeviceProfileQuickActions(device) {
    const target =
        document.getElementById("heroQuickActions");

    if (!target) {
        return;
    }

    const managementUrl =
        String(
            device?.management_url || ""
        ).trim();

    if (!device || !managementUrl) {
        target.innerHTML = "";
        return;
    }

    target.innerHTML = `
        <a
          class="device-hero-action"
          href="${esc(managementUrl)}"
          target="_blank"
          rel="noopener noreferrer"
          title="${esc(managementUrl)}"
        >
          <span aria-hidden="true">🌐</span>
          <span>Open Web UI</span>
          <span
            class="device-hero-action-arrow"
            aria-hidden="true"
          >
            ↗
          </span>
        </a>
    `;
}


function deviceRelationshipInspector(device) {
    if (
        !device ||
        typeof buildTopologyTree !== "function"
    ) {
        return "";
    }

    const tree = buildTopologyTree(
        Array.isArray(devices)
            ? devices
            : [],
        Array.isArray(infrastructure)
            ? infrastructure
            : [],
        canonicalRelationships
    );

    const node = tree.getNode(device.ip);

    if (!node) {
        return "";
    }

    const parent = node.parent;
    const path = tree.pathTo(device.ip);
    const children = node.children || [];

    const source =
        node.relationship?.source ||
        "unknown";

    const confidence =
        Number(
            node.relationship?.confidence || 0
        );

    const locked =
        Boolean(
            node.relationship?.locked
        );

    const transport = node.transport
        ? (
            node.transport
                .charAt(0)
                .toUpperCase() +
            node.transport.slice(1)
        )
        : "Unknown";

    const sourceLabel = {
        manual: "Manual",
        infrastructure: "Configured infrastructure",
        generic: "Generic inference",
        agent: "Agent",
        learned: "Learned",
        inferred: "Inferred",
        unknown: "Unknown"
    }[source] || source;

    const parentHtml = parent
        ? `
            <button
              type="button"
              class="relationship-device-link"
              data-relationship-ip="${esc(parent.ip)}"
            >
              ${esc(
                  deviceProfileName(
                      parent.device
                  )
              )}
            </button>
          `
        : `
            <span class="muted">
              Root / unassigned
            </span>
          `;

    const pathHtml = path.length
        ? path.map((pathNode, index) => {
            const current =
                index === path.length - 1;

            const presentation =
                typeof deviceTypeDetails === "function"
                    ? deviceTypeDetails(
                        pathNode.device?.device_type ||
                        "unknown"
                    )
                    : {
                        icon: "•",
                        colour: "#64748b"
                    };

            const nodeContents = `
                <span
                  class="relationship-path-icon"
                  style="
                    --relationship-path-colour:
                    ${presentation.colour}
                  "
                  aria-hidden="true"
                >
                  ${presentation.icon}
                </span>

                <span class="relationship-path-copy">
                  <strong>
                    ${esc(
                        deviceProfileName(
                            pathNode.device
                        )
                    )}
                  </strong>

                  <small>
                    ${esc(pathNode.ip)}
                  </small>
                </span>
            `;

            return `
              <div class="relationship-path-step">
                ${
                    index
                        ? `
                            <span
                              class="relationship-path-connector"
                              aria-hidden="true"
                            >
                              <span></span>
                              <b>▼</b>
                            </span>
                          `
                        : ""
                }

                ${
                    current
                        ? `
                            <div
                              class="
                                relationship-path-device
                                relationship-path-current
                              "
                            >
                              ${nodeContents}
                            </div>
                          `
                        : `
                            <button
                              type="button"
                              class="
                                relationship-device-link
                                relationship-path-device
                              "
                              data-relationship-ip="${esc(
                                  pathNode.ip
                              )}"
                            >
                              ${nodeContents}
                            </button>
                          `
                }
              </div>
            `;
        }).join("")
        : `
            <span class="muted">
              No dependency path available.
            </span>
          `;

    return `
      <section class="relationship-inspector">
        <div class="relationship-inspector-heading">
          <div>
            <h3>Network Topology</h3>

            <p class="muted">
              How this device is connected within the
              BEACN network graph.
            </p>
          </div>

        </div>

        <div class="relationship-inspector-grid">
          <div class="relationship-stat">
            <small>Parent</small>
            <strong>${parentHtml}</strong>
          </div>

          <div class="relationship-stat">
            <small>Transport</small>

            <strong>
              <span
                class="
                  relationship-transport
                  relationship-transport-${esc(
                    node.transport || 'unknown'
                  )}
                "
              >
                ${esc(transport)}
              </span>
            </strong>
          </div>

          <div class="relationship-stat">
            <small>Confidence</small>

            <strong>
              ${confidence}%
              ${
                confidence === 100
                  ? '<span class="relationship-verified">Verified</span>'
                  : ''
              }
            </strong>
          </div>

          <div class="relationship-stat">
            <small>Relationship source</small>

            <strong>
              ${esc(sourceLabel)}

              ${
                locked
                  ? `
                      <span
                        class="relationship-lock"
                        title="This relationship is manually locked"
                      >
                        🔒
                      </span>
                    `
                  : ''
              }
            </strong>
          </div>

          <div class="relationship-stat">
            <small>Connected devices</small>
            <strong>${children.length}</strong>
          </div>

          <div class="relationship-stat">
            <small>Tree depth</small>
            <strong>${Number(node.depth || 0)}</strong>
          </div>
        </div>

        <div class="relationship-section">
          <h4>Dependency path</h4>

          <div class="relationship-path">
            ${pathHtml}
          </div>
        </div>

        ${
            device.notes
                ? `
                    <div class="relationship-section">
                      <h4>Notes</h4>

                      <div class="relationship-notes">
                        ${esc(device.notes)}
                      </div>
                    </div>
                  `
                : ""
        }
      </section>
    `;
}


function bindDeviceProfileLinks() {
    document
        .querySelectorAll(
            "[data-relationship-ip]"
        )
        .forEach(button => {
            button.addEventListener(
                "click",
                async () => {
                    const ip =
                        button.dataset.relationshipIp;

                    if (!ip) {
                        return;
                    }

                    if (
                        typeof selectTopologyDevice ===
                        "function"
                    ) {
                        await selectTopologyDevice(ip);
                        return;
                    }

                    renderDeviceOptions(ip);
                    select.value = ip;

                    select.dispatchEvent(
                        new Event("change")
                    );
                }
            );
        });
}


window.renderDeviceProfileQuickActions =
    renderDeviceProfileQuickActions;

window.deviceRelationshipInspector =
    deviceRelationshipInspector;

window.bindDeviceProfileLinks =
    bindDeviceProfileLinks;
