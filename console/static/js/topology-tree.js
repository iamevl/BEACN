"use strict";

/*
 * ------------------------------------------------------------------
 * BEACN Relationship / Topology Tree Engine
 *
 * IMPORTANT PRECEDENCE RULE
 *
 * Manual configuration is authoritative.
 *
 * The engine MUST NEVER replace:
 *
 *   display_name
 *   device_type
 *   connection_method
 *   connection_parent_ip
 *
 * when their corresponding source is manual.
 *
 * Automatic inference is read-only and ephemeral. It is used only
 * to construct a relationship model for presentation/analysis.
 *
 * Nothing in this module writes to the BEACN database.
 * ------------------------------------------------------------------
 */


const TOPOLOGY_RELATIONSHIP_CONFIDENCE = {
    manual: 100,
    agent: 100,
    learned: 85,
    inferred: 60,
    unknown: 0
};


function topologyTreeDeviceName(device) {
    return (
        device?.display_name ||
        device?.hostname ||
        device?.ip ||
        "Unknown device"
    );
}


function topologyTreeNormalise(value) {
    return String(value || "")
        .trim()
        .toLowerCase();
}


function topologyTreeConnectionMethod(device) {
    const method = topologyTreeNormalise(
        device?.connection_method
    );

    if (
        method === "wired" ||
        method === "wireless"
    ) {
        return method;
    }

    return null;
}


function topologyTreeIsManual(device) {
    return (
        topologyTreeNormalise(
            device?.connection_source
        ) === "manual"
    );
}


function topologyTreeCreateNode(device) {
    return {
        id: device?.id || device?.ip || "",
        ip: device?.ip || "",
        device,

        parent: null,
        children: [],

        transport: null,

        relationship: {
            source: "unknown",
            confidence: 0,
            locked: false,
            reason: "No relationship has been resolved."
        },

        depth: 0,

        flags: {
            router: device?.device_type === "router",
            infrastructure: [
                "router",
                "switch",
                "access_point"
            ].includes(device?.device_type),
            manual: topologyTreeIsManual(device),
            core_service: false,
            unassigned: false
        }
    };
}


function topologyTreeSetRelationship(
    node,
    parent,
    {
        transport = null,
        source = "unknown",
        confidence = null,
        locked = false,
        reason = ""
    } = {}
) {
    if (!node || !parent) {
        return false;
    }

    /*
     * Never replace a locked/manual relationship.
     */
    if (
        node.relationship.locked &&
        node.parent
    ) {
        return false;
    }

    /*
     * Guard against self-parenting.
     */
    if (node === parent) {
        return false;
    }

    node.parent = parent;
    node.transport = transport;

    node.relationship = {
        source,
        confidence:
            confidence ??
            TOPOLOGY_RELATIONSHIP_CONFIDENCE[
                source
            ] ??
            0,
        locked,
        reason
    };

    if (!parent.children.includes(node)) {
        parent.children.push(node);
    }

    return true;
}


function topologyTreeWouldCreateCycle(
    node,
    prospectiveParent
) {
    let cursor = prospectiveParent;
    const visited = new Set();

    while (cursor) {
        if (cursor === node) {
            return true;
        }

        if (visited.has(cursor)) {
            return true;
        }

        visited.add(cursor);
        cursor = cursor.parent;
    }

    return false;
}


function topologyTreeApplyManualRelationships(
    inventory,
    nodes
) {
    const unresolved = [];

    inventory.forEach(device => {
        if (!topologyTreeIsManual(device)) {
            return;
        }

        const node = nodes.get(device.ip);

        if (!node) {
            return;
        }

        const parentIp = String(
            device.connection_parent_ip || ""
        ).trim();

        const transport =
            topologyTreeConnectionMethod(device);

        if (!parentIp || !transport) {
            unresolved.push({
                device,
                reason:
                    "Manual relationship is incomplete."
            });

            return;
        }

        const parent = nodes.get(parentIp);

        if (!parent) {
            unresolved.push({
                device,
                reason:
                    `Manual parent ${parentIp} is not currently present.`
            });

            return;
        }

        if (
            topologyTreeWouldCreateCycle(
                node,
                parent
            )
        ) {
            unresolved.push({
                device,
                reason:
                    "Manual relationship would create a cycle."
            });

            return;
        }

        topologyTreeSetRelationship(
            node,
            parent,
            {
                transport,
                source: "manual",
                confidence: 100,
                locked: true,
                reason:
                    "Relationship explicitly assigned by the user."
            }
        );
    });

    return unresolved;
}


function topologyTreeIdentifyCoreServices(
    inventory,
    nodes,
    router
) {
    if (!router) {
        return [];
    }

    const services = [];

    inventory.forEach(device => {
        const node = nodes.get(device.ip);

        if (!node) {
            return;
        }

        const evidence = [
            device.display_name,
            device.hostname
        ]
            .filter(Boolean)
            .join(" ")
            .toLowerCase();

        const isPiHole =
            evidence.includes("pihole") ||
            evidence.includes("pi-hole");

        if (!isPiHole) {
            return;
        }

        /*
         * Core-service classification is logical only.
         * It does NOT modify the physical parent relationship.
         */
        node.flags.core_service = true;

        services.push({
            node,
            service_type: "dns",
            logical_parent: router,
            reason:
                "Recognised Pi-hole DNS service."
        });
    });

    return services;
}


function topologyTreeConservativeInference(
    inventory,
    nodes,
    router
) {
    /*
     * Relationship Engine v1 intentionally performs almost no
     * speculative attachment.
     *
     * Manual relationships have already been applied.
     *
     * Devices without sufficient evidence remain unassigned.
     *
     * Future evidence providers can plug in here:
     *
     *   ASUS / Merlin Wi-Fi associations
     *   LLDP
     *   CDP
     *   switch MAC tables
     *   ARP / neighbour data
     *   agent telemetry
     *
     * Until then, uncertainty is preferable to a false topology.
     */

    if (!router) {
        return;
    }

    inventory.forEach(device => {
        const node = nodes.get(device.ip);

        if (!node || node.parent) {
            return;
        }

        /*
         * Routers are roots, never inferred beneath themselves.
         */
        if (node.flags.router) {
            return;
        }

        /*
         * Do not infer over anything that contains manual metadata,
         * even if the relationship is currently incomplete.
         */
        if (node.flags.manual) {
            return;
        }

        /*
         * At v1 we deliberately leave these unresolved.
         */
    });
}


function topologyTreeAssignDepths(root) {
    if (!root) {
        return;
    }

    const queue = [{
        node: root,
        depth: 0
    }];

    const visited = new Set();

    while (queue.length) {
        const item = queue.shift();

        if (visited.has(item.node)) {
            continue;
        }

        visited.add(item.node);

        item.node.depth = item.depth;

        item.node.children.forEach(child => {
            queue.push({
                node: child,
                depth: item.depth + 1
            });
        });
    }
}


function topologyTreeSortChildren(nodes) {
    nodes.forEach(node => {
        node.children.sort((left, right) => {
            const leftInfrastructure =
                Number(
                    left.flags.infrastructure
                );

            const rightInfrastructure =
                Number(
                    right.flags.infrastructure
                );

            if (
                leftInfrastructure !==
                rightInfrastructure
            ) {
                return (
                    rightInfrastructure -
                    leftInfrastructure
                );
            }

            return topologyTreeDeviceName(
                left.device
            ).localeCompare(
                topologyTreeDeviceName(
                    right.device
                )
            );
        });
    });
}


function topologyTreeFindUnassigned(
    nodes,
    root
) {
    const unassigned = [];

    nodes.forEach(node => {
        if (node === root) {
            return;
        }

        if (!node.parent) {
            node.flags.unassigned = true;
            unassigned.push(node);
        }
    });

    return unassigned;
}


function topologyTreeBuildPath(node) {
    if (!node) {
        return [];
    }

    const path = [];
    const visited = new Set();

    let cursor = node;

    while (cursor) {
        if (visited.has(cursor)) {
            break;
        }

        visited.add(cursor);
        path.unshift(cursor);

        cursor = cursor.parent;
    }

    return path;
}


function topologyTreeDescendants(node) {
    if (!node) {
        return [];
    }

    const result = [];
    const queue = [...node.children];
    const visited = new Set();

    while (queue.length) {
        const child = queue.shift();

        if (visited.has(child)) {
            continue;
        }

        visited.add(child);
        result.push(child);

        queue.push(...child.children);
    }

    return result;
}


function topologyTreeDiagnostics(tree) {
    return {
        devices: tree.nodes.size,

        root:
            tree.root?.ip || null,

        manual_relationships:
            [...tree.nodes.values()]
                .filter(
                    node =>
                        node.relationship.source ===
                        "manual"
                )
                .length,

        unassigned:
            tree.unassigned.length,

        core_services:
            tree.coreServices.length,

        max_depth:
            Math.max(
                0,
                ...[...tree.nodes.values()]
                    .map(node => node.depth)
            ),

        unresolved_manual:
            tree.unresolvedManual.length
    };
}


function buildTopologyTree(inventory = []) {
    const safeInventory =
        Array.isArray(inventory)
            ? inventory
            : [];

    const nodes = new Map();

    safeInventory.forEach(device => {
        if (!device?.ip) {
            return;
        }

        nodes.set(
            device.ip,
            topologyTreeCreateNode(device)
        );
    });

    const routers =
        safeInventory.filter(
            device =>
                device.device_type === "router"
        );

    const root =
        routers.length
            ? nodes.get(routers[0].ip)
            : null;

    const unresolvedManual =
        topologyTreeApplyManualRelationships(
            safeInventory,
            nodes
        );

    const coreServices =
        topologyTreeIdentifyCoreServices(
            safeInventory,
            nodes,
            root
        );

    topologyTreeConservativeInference(
        safeInventory,
        nodes,
        root
    );

    topologyTreeSortChildren(nodes);
    topologyTreeAssignDepths(root);

    const unassigned =
        topologyTreeFindUnassigned(
            nodes,
            root
        );

    const tree = {
        root,
        nodes,
        coreServices,
        unassigned,
        unresolvedManual,

        getNode(ip) {
            return nodes.get(ip) || null;
        },

        pathTo(ip) {
            return topologyTreeBuildPath(
                nodes.get(ip)
            );
        },

        descendantsOf(ip) {
            return topologyTreeDescendants(
                nodes.get(ip)
            );
        }
    };

    tree.diagnostics =
        topologyTreeDiagnostics(tree);

    return tree;
}


window.buildTopologyTree =
    buildTopologyTree;

window.topologyTreeDiagnostics =
    topologyTreeDiagnostics;
