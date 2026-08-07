"use strict";


function snmpRate(bitsPerSecond) {
  const value = Number(bitsPerSecond || 0);

  if (!value) {
    return 'Unknown speed';
  }

  if (value >= 1_000_000_000) {
    return `${(value / 1_000_000_000).toFixed(
      value % 1_000_000_000 ? 1 : 0
    )} Gbps`;
  }

  if (value >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(
      value % 1_000_000 ? 1 : 0
    )} Mbps`;
  }

  return `${value} bps`;
}


function renderSnmpPanel(snmp) {
  if (!snmp?.available) {
    return '';
  }

  const system = snmp.system || {};
  const interfaces = Array.isArray(snmp.interfaces)
    ? snmp.interfaces
    : [];

  const security =
    snmp.version === '3'
      ? 'Secure SNMPv3 · authPriv'
      : `SNMP ${esc(snmp.version || '')}`;

  const interfaceCards = interfaces.length
    ? interfaces.map(iface => {
        const state =
          iface.oper_status?.state ||
          'unknown';

        const up = state === 'up';

        return `
          <div class="snmp-interface-card">
            <div class="row-between">
              <div>
                <strong>
                  ${esc(iface.name || 'Interface')}
                </strong>

                <div class="muted snmp-interface-kind">
                  ${
                    iface.kind === 'logical'
                      ? 'Logical aggregate'
                      : 'Physical interface'
                  }
                </div>
              </div>

              <span class="${up ? 'good' : 'bad'}">
                ${esc(state.toUpperCase())}
              </span>
            </div>

            <div class="snmp-interface-meta">
              <span>
                ${esc(snmpRate(iface.speed_bps))}
              </span>

              <span>
                MTU ${esc(iface.mtu ?? '—')}
              </span>
            </div>

            ${
              iface.mac
                ? `
                    <div class="snmp-interface-mac">
                      ${esc(iface.mac)}
                    </div>
                  `
                : ''
            }
          </div>
        `;
      }).join('')
    : `
        <div class="empty">
          No meaningful SNMP interfaces reported.
        </div>
      `;

  return `
    <section class="snmp-panel">
      <div class="snmp-panel-heading">
        <div>
          <h3>SNMP</h3>

          <p class="muted">
            ${esc(security)}
          </p>
        </div>

        <span class="badge">
          ${interfaces.length} interfaces
        </span>
      </div>

      <div class="snmp-system-strip">
        <div>
          <small>System name</small>
          <strong>
            ${esc(system.name || 'Unknown')}
          </strong>
        </div>

        <div>
          <small>Object ID</small>
          <strong>
            ${esc(system.object_id || 'Unknown')}
          </strong>
        </div>

        <div>
          <small>SNMP agent uptime</small>
          <strong>
            ${
              system.uptime_seconds != null
                ? uptime(system.uptime_seconds)
                : '—'
            }
          </strong>
        </div>
      </div>

      ${
        system.description
          ? `
              <div class="snmp-description">
                ${esc(system.description)}
              </div>
            `
          : ''
      }

      <h4 class="snmp-interface-heading">
        SNMP Interfaces
      </h4>

      <div class="snmp-interface-grid">
        ${interfaceCards}
      </div>
    </section>
  `;
}


window.renderSnmpPanel =
  renderSnmpPanel;
