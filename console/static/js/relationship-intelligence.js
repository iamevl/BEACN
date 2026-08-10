"use strict";


function riEsc(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


function relationshipConfidenceClass(value) {
    const confidence = Number(value || 0);

    if (confidence >= 90) {
        return "relationship-confidence-high";
    }

    if (confidence >= 70) {
        return "relationship-confidence-medium";
    }

    return "relationship-confidence-low";
}


function relationshipStatusIcon(status) {
    return status === "healthy"
        ? "✓"
        : "○";
}


function renderRelationshipSummary(payload) {
    const target =
        document.getElementById(
            "relationshipSummary"
        );

    if (!target) {
        return;
    }

    const summary = payload.summary || {};

    const items = [
        {
            label: "Relationships",
            value: summary.relationships ?? 0,
            hint: "Evidence-backed links"
        },
        {
            label: "Device links",
            value: summary.device_relationships ?? 0,
            hint: "Resolved devices"
        },
        {
            label: "Unresolved",
            value: summary.unresolved_devices ?? 0,
            hint: "Awaiting evidence"
        },
        {
            label: "Access points",
            value: summary.unresolved_access_points ?? 0,
            hint: "Parent not yet proven"
        },
        {
            label: "Providers",
            value: summary.providers ?? 0,
            hint: "Active evidence sources"
        },
        {
            label: "Evidence",
            value: summary.evidence_items ?? 0,
            hint: "Evidence observations"
        }
    ];

    target.innerHTML = items
        .map(item => `
            <article class="relationship-summary-card">
              <span class="relationship-summary-label">
                ${riEsc(item.label)}
              </span>

              <strong class="relationship-summary-value">
                ${riEsc(item.value)}
              </strong>

              <small class="muted">
                ${riEsc(item.hint)}
              </small>
            </article>
        `)
        .join("");
}


function renderRelationshipProviders(payload) {
    const target =
        document.getElementById(
            "relationshipProviders"
        );

    if (!target) {
        return;
    }

    const providers =
        Array.isArray(payload.providers)
            ? payload.providers
            : [];

    if (!providers.length) {
        target.innerHTML = `
            <div class="empty">
              No evidence providers are active.
            </div>
        `;
        return;
    }

    target.innerHTML = providers
        .map(provider => `
            <div class="relationship-provider-row">
              <div class="relationship-provider-main">
                <span
                  class="
                    relationship-provider-status
                    ${
                        provider.status === "healthy"
                            ? "good"
                            : "muted"
                    }
                  "
                >
                  ${relationshipStatusIcon(
                      provider.status
                  )}
                </span>

                <div>
                  <strong>
                    ${riEsc(
                        provider.label ||
                        provider.name
                    )}
                  </strong>

                  <small class="muted">
                    ${riEsc(provider.status || "unknown")}
                  </small>
                </div>
              </div>

              <div class="relationship-provider-counts">
                <span>
                  ${riEsc(
                      provider.relationship_count ?? 0
                  )}
                  relationships
                </span>

                <small class="muted">
                  ${riEsc(
                      provider.evidence_count ?? 0
                  )}
                  evidence
                </small>
              </div>
            </div>
        `)
        .join("");
}


function renderRelationshipResolution(payload) {
    const target =
        document.getElementById(
            "relationshipResolution"
        );

    if (!target) {
        return;
    }

    const summary = payload.summary || {};

    const unresolved =
        Array.isArray(payload.unresolved)
            ? payload.unresolved
            : [];

    const examples = unresolved
        .filter(item =>
            item.presentation_role !==
            "core_service"
        )
        .slice(0, 6);

    target.innerHTML = `
        <div class="relationship-resolution-stats">
          <div>
            <strong>
              ${riEsc(
                  summary.unresolved_endpoints ?? 0
              )}
            </strong>
            <span>Endpoints</span>
          </div>

          <div>
            <strong>
              ${riEsc(
                  summary.unresolved_access_points ?? 0
              )}
            </strong>
            <span>Access points</span>
          </div>

          <div>
            <strong>
              ${riEsc(
                  summary.core_services_without_parent ?? 0
              )}
            </strong>
            <span>Core services</span>
          </div>
        </div>

        ${
            examples.length
                ? `
                    <div class="relationship-unresolved-preview">
                      ${examples.map(item => `
                          <div class="relationship-unresolved-row">
                            <div>
                              <strong>
                                ${riEsc(item.name)}
                              </strong>

                              <small class="muted">
                                ${riEsc(
                                    item.device_type ||
                                    "unknown"
                                )}
                                ·
                                ${riEsc(item.ip || "")}
                              </small>
                            </div>

                            <span class="badge">
                              Waiting
                            </span>
                          </div>
                      `).join("")}
                    </div>
                  `
                : `
                    <div class="good relationship-all-resolved">
                      All devices have relationship evidence.
                    </div>
                  `
        }
    `;
}


function renderRelationshipTable(payload) {
    const target =
        document.getElementById(
            "relationshipTable"
        );

    const count =
        document.getElementById(
            "relationshipTableCount"
        );

    if (!target) {
        return;
    }

    const relationships =
        Array.isArray(payload.relationships)
            ? payload.relationships
            : [];

    if (count) {
        count.textContent =
            `${relationships.length}`;
    }

    if (!relationships.length) {
        target.innerHTML = `
            <div class="empty">
              No resolved relationships yet.
            </div>
        `;
        return;
    }

    const sorted = [...relationships]
        .sort((left, right) =>
            String(
                left.subject?.name || ""
            ).localeCompare(
                String(
                    right.subject?.name || ""
                )
            )
        );

    target.innerHTML = `
        <table class="relationship-table">
          <thead>
            <tr>
              <th>Subject</th>
              <th>Parent</th>
              <th>Transport</th>
              <th>Evidence</th>
              <th>Confidence</th>
            </tr>
          </thead>

          <tbody>
            ${sorted.map(item => `
                <tr>
                  <td>
                    <strong>
                      ${riEsc(
                          item.subject?.name ||
                          item.subject_ref
                      )}
                    </strong>

                    <small class="muted relationship-table-subtext">
                      ${riEsc(
                          item.subject?.ip ||
                          item.subject_ref
                      )}
                    </small>
                  </td>

                  <td>
                    <strong>
                      ${riEsc(
                          item.parent?.name ||
                          item.parent_ref
                      )}
                    </strong>
                  </td>

                  <td>
                    <span class="badge">
                      ${riEsc(
                          item.transport || "unknown"
                      )}
                    </span>
                  </td>

                  <td>
                    <strong>
                      ${riEsc(
                          item.provider_label ||
                          item.provider
                      )}
                    </strong>

                    <small class="muted relationship-table-subtext">
                      ${riEsc(
                          item.reason_label ||
                          item.reason
                      )}
                    </small>
                  </td>

                  <td>
                    <span
                      class="
                        relationship-confidence
                        ${relationshipConfidenceClass(
                            item.confidence
                        )}
                      "
                    >
                      ${riEsc(item.confidence)}%
                    </span>
                  </td>
                </tr>
            `).join("")}
          </tbody>
        </table>
    `;
}


async function refreshRelationshipIntelligence() {
    const engineState =
        document.getElementById(
            "relationshipEngineState"
        );

    try {
        const response = await fetch(
            "/api/relationships",
            {
                cache: "no-store"
            }
        );

        if (!response.ok) {
            throw new Error(
                `HTTP ${response.status}`
            );
        }

        const payload =
            await response.json();

        if (!payload.ok) {
            throw new Error(
                "Relationship Manager returned an error."
            );
        }

        if (engineState) {
            engineState.textContent =
                payload.engine?.status === "healthy"
                    ? "● Engine healthy"
                    : "● Engine degraded";

            engineState.classList.toggle(
                "good",
                payload.engine?.status === "healthy"
            );
        }

        renderRelationshipSummary(payload);
        renderRelationshipProviders(payload);
        renderRelationshipResolution(payload);
        renderRelationshipTable(payload);

    } catch (error) {
        if (engineState) {
            engineState.textContent =
                `Engine unavailable · ${error.message}`;

            engineState.classList.remove(
                "good"
            );
        }
    }
}


document.addEventListener(
    "DOMContentLoaded",
    () => {
        refreshRelationshipIntelligence();

        setInterval(
            refreshRelationshipIntelligence,
            15000
        );
    }
);
