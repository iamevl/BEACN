"use strict";

/*
 * ------------------------------------------------------------------
 * BEACN Relationship / Topology Tree Engine v2
 *
 * Manual configuration is authoritative.
 *
 * Nodes may represent:
 *
 *   device:<ip>
 *   infra:<uuid>
 *
 * Infrastructure objects do not require an IP address.
 *
 * Automatic inference remains read-only and must never override
 * explicit user relationships.
 * ------------------------------------------------------------------
 */


const TOPOLOGY_RELATIONSHIP_CONFIDENCE = {
    manual: 100,
    agent: 100,
    learned: 85,
    inferred: 60,
    unknown: 0
};


function topologyTreeNormalise(value) {
    return String(value || "")
        .trim()
        .toLowerCase();
}


function topologyTreeDeviceName(device) {
    return (
        device?.display_name ||
        device?.hostname ||
        device?.name ||
        device?.ip ||
        "Unknown device"
    );
}


function topologyTreeConnectionMethod(subject) {
    const method = topologyTreeNormalise(
        subject?.connection_method
    );

    if (
        method === "wired" ||
        method === "wireless" ||
        method === "virtual"
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


function topologyTreeDeviceRef(device) {
    const ip = String(device?.ip || "").trim();

    return ip
        ? `device:${ip}`
        : "";
}


function topologyTreeInfrastructureRef(item) {
    if (item?.ref) {
        return String(item.ref);
    }

    if (item?.id) {
        return `infra:${item.id}`;
    }

    return "";
}


function topologyTreeInfrastructureDevice(item) {
    const interfaces =
        Array.isArray(item?.interfaces)
            ? item.interfaces
            : [];

    const primaryInterface =
        interfaces.find(entry => entry?.address) ||
        interfaces[0] ||
        null;

    const typeMap = {
        internet: "internet",
        isp_gateway: "router",
        router: "router",
        firewall: "router",
        switch: "switch",
        access_point: "access_point",
        patch_panel: "switch",
        ups: "ups",
        rack: "infrastructure",
        poe_injector: "infrastructure",
        other: "infrastructure"
    };

    return {
        id: item?.id || "",
        object_ref:
            topologyTreeInfrastructureRef(item),

        object_kind: "infrastructure",

        display_name:
            item?.name || "Infrastructure",

        hostname: null,

        ip:
            primaryInterface?.address || "",

        mac: null,

        device_type:
            typeMap[item?.infrastructure_type] ||
            "infrastructure",

        infrastructure_type:
            item?.infrastructure_type || "other",

        manufacturer:
            item?.manufacturer || null,

        model:
            item?.model || null,

        managed:
            item?.managed,

        port_count:
            item?.port_count,

        location:
            item?.location || null,

        management_url:
            item?.management_url || null,

        notes:
            item?.notes || null,

        interfaces,

        connection_method:
            item?.connection_method || "wired",

        connection_parent_ref:
            item?.parent_ref || "",

        connection_parent_ip: "",

        connection_source: "manual",

        is_online: 1,

        manual_infrastructure: true,

        infrastructure_record: item
    };
}


function topologyTreeCreateNode(
    device,
    {
        ref = "",
        objectKind = "device"
    } = {}
) {
    const resolvedRef =
        ref ||
        topologyTreeDeviceRef(device) ||
        device?.object_ref ||
        "";

    return {
        id: resolvedRef,
        ref: resolvedRef,

        ip: device?.ip || "",

        object_kind: objectKind,

        device,

        parent: null,
        children: [],

        transport: null,

        relationship: {
            source: "unknown",
            confidence: 0,
            locked: false,
            reason:
                "No relationship has been resolved."
        },

        depth: 0,

        flags: {
            router:
                device?.device_type === "router",

            infrastructure:
                objectKind === "infrastructure" ||
                [
                    "router",
                    "switch",
                    "access_point"
                ].includes(device?.device_type),

            manual:
                objectKind === "infrastructure" ||
                topologyTreeIsManual(device),

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
    if (!node || !parent || node === parent) {
        return false;
    }

    if (
        node.relationship.locked &&
        node.parent
    ) {
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


function topologyTreeResolveNode(
    value,
    nodes,
    ipNodes
) {
    const raw = String(value || "").trim();

    if (!raw) {
        return null;
    }

    if (nodes.has(raw)) {
        return nodes.get(raw);
    }

    if (raw.startsWith("device:")) {
        return nodes.get(raw) || null;
    }

    if (raw.startsWith("infra:")) {
        return nodes.get(raw) || null;
    }

    return (
        ipNodes.get(raw) ||
        nodes.get(`device:${raw}`) ||
        null
    );
}


function topologyTreeApplyInfrastructureRelationships(
    infrastructure,
    nodes,
    ipNodes
) {
    const unresolved = [];

    infrastructure.forEach(item => {
        const ref =
            topologyTreeInfrastructureRef(item);

        const node = nodes.get(ref);

        if (!node) {
            return;
        }

        const parentRef =
            String(item?.parent_ref || "").trim();

        /*
         * A root object such as Internet deliberately
         * has no parent.
         */
        if (!parentRef) {
            return;
        }

        const parent =
            topologyTreeResolveNode(
                parentRef,
                nodes,
                ipNodes
            );

        if (!parent) {
            unresolved.push({
                subject: item,
                reason:
                    `Infrastructure parent ${parentRef} is not present.`
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
                subject: item,
                reason:
                    "Infrastructure relationship would create a cycle."
            });

            return;
        }

        topologyTreeSetRelationship(
            node,
            parent,
            {
                transport:
                    topologyTreeConnectionMethod(
                        item
                    ) || "wired",

                source: "manual",
                confidence: 100,
                locked: true,

                reason:
                    "Infrastructure relationship explicitly assigned by the user."
            }
        );
    });

    return unresolved;
}


function topologyTreeApplyManualRelationships(
    inventory,
    nodes,
    ipNodes
) {
    const unresolved = [];

    inventory.forEach(device => {
        if (!topologyTreeIsManual(device)) {
            return;
        }

        const node = topologyTreeResolveNode(
            topologyTreeDeviceRef(device),
            nodes,
            ipNodes
        );

        if (!node) {
            return;
        }

        const parentRef =
            String(
                device.connection_parent_ref ||
                (
                    device.connection_parent_ip
                        ? `device:${device.connection_parent_ip}`
                        : ""
                )
            ).trim();

        const transport =
            topologyTreeConnectionMethod(device);

        if (!parentRef || !transport) {
            unresolved.push({
                device,
                reason:
                    "Manual relationship is incomplete."
            });

            return;
        }

        const parent =
            topologyTreeResolveNode(
                parentRef,
                nodes,
                ipNodes
            );

        if (!parent) {
            unresolved.push({
                device,
                reason:
                    `Manual parent ${parentRef} is not currently present.`
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
    ipNodes,
    router
) {
    if (!router) {
        return [];
    }

    const services = [];

    inventory.forEach(device => {
        const node =
            topologyTreeResolveNode(
                topologyTreeDeviceRef(device),
                nodes,
                ipNodes
            );

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


function topologyTreeStronglyWired(device) {
    if (!device) {
        return false;
    }

    const type =
        topologyTreeNormalise(
            device.device_type
        );

    const hostname =
        topologyTreeNormalise(
            device.hostname
        );

    const name =
        topologyTreeNormalise(
            device.display_name
        );

    /*
     * These device roles are normally wired infrastructure
     * endpoints. This is intentionally conservative.
     */
    if (
        [
            "nas",
            "server",
            "media_tuner",
            "ups"
        ].includes(type)
    ) {
        return true;
    }

    /*
     * HDHomeRun devices are wired Ethernet tuners.
     */
    if (
        hostname.startsWith("hdhr-") ||
        name.includes("hd homerun") ||
        name.includes("hdhomerun")
    ) {
        return true;
    }

    /*
     * A computer with a BEACN agent is useful evidence in this
     * environment. We give this a lower inferred confidence than
     * explicit switch/SNMP evidence.
     */
    if (
        type === "computer" &&
        Boolean(device.agent_available)
    ) {
        return true;
    }

    return false;
}


function topologyTreeConservativeInference(
    inventory,
    nodes,
    ipNodes,
    router
) {
    if (!router) {
        return;
    }

    /*
     * Automatic upstream inference.
     *
     * If exactly one ISP gateway exists and the primary router
     * has no parent, it is safe to place the router beneath that
     * gateway.
     *
     * Explicit/manual relationships always win because this only
     * runs when router.parent is empty.
     */
    if (!router.parent) {
        const ispGateways =
            [...nodes.values()]
                .filter(node =>
                    node.object_kind === "infrastructure" &&
                    node.device?.infrastructure_type ===
                        "isp_gateway"
                );

        if (ispGateways.length === 1) {
            const gateway = ispGateways[0];

            if (
                !topologyTreeWouldCreateCycle(
                    router,
                    gateway
                )
            ) {
                topologyTreeSetRelationship(
                    router,
                    gateway,
                    {
                        transport: "wired",
                        source: "inferred",
                        confidence: 85,
                        locked: false,
                        reason:
                            "Primary router automatically placed beneath the single known ISP gateway."
                    }
                );
            }
        }
    }

    /*
     * ----------------------------------------------------------
     * Main wired distribution switch
     * ----------------------------------------------------------
     *
     * Prefer a switch already attached directly to the primary
     * router. In the current network this resolves Loft Switch.
     *
     * We only infer downstream relationships if exactly one such
     * switch exists, avoiding guesses on networks with multiple
     * parallel distribution switches.
     */
    const routerSwitches =
        router.children.filter(node =>
            node.device?.device_type === "switch"
        );

    const distributionSwitch =
        routerSwitches.length === 1
            ? routerSwitches[0]
            : null;


    if (distributionSwitch) {
        /*
         * Any unresolved discovered switch can safely become a
         * downstream switch when there is one known distribution
         * switch.
         *
         * Example:
         *
         * ASUS
         *   -> Loft Switch
         *        -> Kal's Room Switch
         */
        [...nodes.values()]
            .filter(node =>
                node.object_kind === "device" &&
                node.device?.device_type === "switch" &&
                node !== distributionSwitch &&
                !node.parent
            )
            .forEach(node => {
                if (
                    topologyTreeWouldCreateCycle(
                        node,
                        distributionSwitch
                    )
                ) {
                    return;
                }

                topologyTreeSetRelationship(
                    node,
                    distributionSwitch,
                    {
                        transport: "wired",
                        source: "inferred",
                        confidence: 70,
                        locked: false,
                        reason:
                            "Unresolved switch placed beneath the single known wired distribution switch."
                    }
                );
            });
    }


    inventory.forEach(device => {
        const node =
            topologyTreeResolveNode(
                topologyTreeDeviceRef(device),
                nodes,
                ipNodes
            );

        if (!node || node.parent) {
            return;
        }

        /*
         * Strong wired endpoint evidence.
         *
         * This deliberately does not attempt to guess televisions,
         * speakers, IoT devices, cameras, phones or laptops.
         */
        if (
            distributionSwitch &&
            topologyTreeStronglyWired(device)
        ) {
            if (
                !topologyTreeWouldCreateCycle(
                    node,
                    distributionSwitch
                )
            ) {
                topologyTreeSetRelationship(
                    node,
                    distributionSwitch,
                    {
                        transport: "wired",
                        source: "inferred",
                        confidence: 65,
                        locked: false,
                        reason:
                            "Device role provides strong wired-endpoint evidence and one distribution switch is known."
                    }
                );

                return;
            }
        }

        if (node.flags.router) {
            return;
        }

        if (node.flags.manual) {
            return;
        }

        /*
         * Deliberately conservative.
         * Unknown physical relationships remain unresolved.
         */
    });
}


function topologyTreeAssignDepths(
    rootNodes
) {
    const queue = rootNodes.map(node => ({
        node,
        depth: 0
    }));

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
        nodes: tree.nodes.size,

        devices:
            [...tree.nodes.values()]
                .filter(node =>
                    node.object_kind === "device"
                )
                .length,

        infrastructure:
            [...tree.nodes.values()]
                .filter(node =>
                    node.object_kind ===
                    "infrastructure"
                )
                .length,

        root:
            tree.root?.ref || null,

        primary_router:
            tree.primaryRouter?.ref || null,

        manual_relationships:
            [...tree.nodes.values()]
                .filter(node =>
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


function buildTopologyTree(
    inventory = [],
    infrastructureInventory = []
) {
    const safeInventory =
        Array.isArray(inventory)
            ? inventory
            : [];

    const safeInfrastructure =
        Array.isArray(infrastructureInventory)
            ? infrastructureInventory
            : [];

    const nodes = new Map();
    const ipNodes = new Map();


    /*
     * Discovered devices.
     */
    safeInventory.forEach(device => {
        const ref =
            topologyTreeDeviceRef(device);

        if (!ref) {
            return;
        }

        const node =
            topologyTreeCreateNode(
                device,
                {
                    ref,
                    objectKind: "device"
                }
            );

        nodes.set(ref, node);

        if (device.ip) {
            ipNodes.set(device.ip, node);
        }
    });


    /*
     * Manual infrastructure objects.
     */
    safeInfrastructure.forEach(item => {
        const ref =
            topologyTreeInfrastructureRef(item);

        if (!ref) {
            return;
        }

        const device =
            topologyTreeInfrastructureDevice(
                item
            );

        const node =
            topologyTreeCreateNode(
                device,
                {
                    ref,
                    objectKind:
                        "infrastructure"
                }
            );

        nodes.set(ref, node);

        if (
            device.ip &&
            !ipNodes.has(device.ip)
        ) {
            ipNodes.set(device.ip, node);
        }
    });


    const routers =
        safeInventory.filter(device =>
            device.device_type === "router"
        );

    const primaryRouter =
        routers.length
            ? topologyTreeResolveNode(
                topologyTreeDeviceRef(
                    routers[0]
                ),
                nodes,
                ipNodes
            )
            : null;


    const unresolvedInfrastructure =
        topologyTreeApplyInfrastructureRelationships(
            safeInfrastructure,
            nodes,
            ipNodes
        );


    const unresolvedDevices =
        topologyTreeApplyManualRelationships(
            safeInventory,
            nodes,
            ipNodes
        );


    const coreServices =
        topologyTreeIdentifyCoreServices(
            safeInventory,
            nodes,
            ipNodes,
            primaryRouter
        );


    topologyTreeConservativeInference(
        safeInventory,
        nodes,
        ipNodes,
        primaryRouter
    );


    topologyTreeSortChildren(nodes);


    const roots =
        [...nodes.values()]
            .filter(node => !node.parent);

    topologyTreeAssignDepths(roots);


    /*
     * Preserve legacy UI behaviour:
     * only unresolved discovered devices appear
     * in the Unassigned Devices panel.
     */
    const unassigned =
        [...nodes.values()]
            .filter(node =>
                node.object_kind === "device" &&
                node !== primaryRouter &&
                !node.parent
            );

    unassigned.forEach(node => {
        node.flags.unassigned = true;
    });


    /*
     * Canonical graph root.
     *
     * Prefer an Internet infrastructure object.
     * Otherwise retain the primary router.
     */
    const internetRoot =
        [...nodes.values()]
            .find(node =>
                node.object_kind ===
                    "infrastructure" &&
                node.device
                    ?.infrastructure_type ===
                    "internet"
            );

    const root =
        internetRoot ||
        primaryRouter ||
        roots[0] ||
        null;


    const tree = {
        root,
        primaryRouter,

        roots,
        nodes,
        ipNodes,

        coreServices,
        unassigned,

        unresolvedManual: [
            ...unresolvedInfrastructure,
            ...unresolvedDevices
        ],


        getNode(value) {
            return (
                topologyTreeResolveNode(
                    value,
                    nodes,
                    ipNodes
                )
            );
        },


        pathTo(value) {
            return topologyTreeBuildPath(
                topologyTreeResolveNode(
                    value,
                    nodes,
                    ipNodes
                )
            );
        },


        descendantsOf(value) {
            return topologyTreeDescendants(
                topologyTreeResolveNode(
                    value,
                    nodes,
                    ipNodes
                )
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
