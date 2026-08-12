"use strict";

/*
 * ------------------------------------------------------------------
 * BEACN Canonical Relationship Topology Tree
 *
 * Relationship truth is supplied by /api/relationships.
 *
 * Nodes may represent:
 *
 *   device:<ip>
 *   infra:<uuid>
 *
 * Infrastructure objects do not require an IP address.
 *
 * This module creates presentation nodes and applies canonical
 * winners. It performs no evidence evaluation or parent inference.
 * ------------------------------------------------------------------
 */


function topologyTreeDeviceName(device) {
    return (
        device?.display_name ||
        device?.hostname ||
        device?.name ||
        device?.ip ||
        "Unknown device"
    );
}


function topologyTreeDeviceRef(device) {
    const ip = String(device?.ip || "").trim();

    return ip
        ? `device:${ip}`
        : "";
}


function topologyTreeCanonicalKey(
    objectKind,
    objectId
) {
    const kind = String(objectKind || "").trim();
    const id = String(objectId || "").trim();

    return kind && id
        ? `${kind}:${id}`
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
        id:
            topologyTreeCanonicalKey(
                objectKind,
                device?.id
            ) || resolvedRef,
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
                "No relationship has been resolved.",
            resolution_status: "no_evidence",
            diagnostics: []
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
                false,

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
        reason = "",
        resolutionStatus = "resolved"
    } = {}
) {
    if (!node || !parent || node === parent) {
        return false;
    }

    node.parent = parent;
    node.transport = transport;

    node.relationship = {
        source,

        confidence: confidence ?? 0,
        locked,
        reason,
        resolution_status: resolutionStatus,
        diagnostics: []
    };

    if (!parent.children.includes(node)) {
        parent.children.push(node);
    }

    return true;
}


function topologyTreeResolveNode(
    identity,
    canonicalNodes,
    nodes,
    ipNodes
) {
    const descriptor =
        typeof identity === "object" && identity
            ? identity
            : {ref: identity};
    const objectKind = descriptor.objectKind || "";
    const objectId = descriptor.objectId || "";
    const ref = descriptor.ref || "";
    const canonicalKey =
        topologyTreeCanonicalKey(
            objectKind,
            objectId
        );

    if (
        canonicalKey &&
        canonicalNodes.has(canonicalKey)
    ) {
        return canonicalNodes.get(canonicalKey);
    }

    const compatibilityRef =
        String(ref || "").trim();

    if (!compatibilityRef) {
        return null;
    }

    if (canonicalNodes.has(compatibilityRef)) {
        return canonicalNodes.get(compatibilityRef);
    }

    if (nodes.has(compatibilityRef)) {
        return nodes.get(compatibilityRef);
    }

    if (compatibilityRef.startsWith("device:")) {
        return nodes.get(compatibilityRef) || null;
    }

    return (
        ipNodes.get(compatibilityRef) ||
        nodes.get(`device:${compatibilityRef}`) ||
        null
    );
}


function topologyTreeApplyCanonicalRelationships(
    canonicalPayload,
    canonicalNodes,
    nodes,
    ipNodes
) {
    const diagnostics = [];
    const relationships =
        Array.isArray(canonicalPayload?.relationships)
            ? canonicalPayload.relationships
            : [];

    relationships.forEach(relationship => {
        if (
            relationship?.resolved !== true ||
            relationship?.resolution_status !==
                "resolved"
        ) {
            return;
        }

        const node = topologyTreeResolveNode({
            objectKind: relationship.subject_kind,
            objectId: relationship.subject_id,
            ref: relationship.subject_ref
        }, canonicalNodes, nodes, ipNodes);

        const parent = topologyTreeResolveNode({
            objectKind: relationship.parent_kind,
            objectId: relationship.parent_id,
            ref: relationship.parent_ref
        }, canonicalNodes, nodes, ipNodes);

        if (!node || !parent || node === parent) {
            diagnostics.push({
                code: "canonical_participant_missing",
                subject_ref:
                    relationship.subject_ref || null,
                parent_ref:
                    relationship.parent_ref || null
            });
            return;
        }

        if (node.parent) {
            diagnostics.push({
                code: "duplicate_canonical_subject",
                subject_ref:
                    relationship.subject_ref || null
            });
            return;
        }

        topologyTreeSetRelationship(node, parent, {
            transport: relationship.transport,
            source: relationship.provider,
            confidence: relationship.confidence,
            locked: [
                "manual",
                "infrastructure"
            ].includes(relationship.provider),
            reason: relationship.reason,
            resolutionStatus:
                relationship.resolution_status
        });

        node.flags.manual =
            relationship.provider === "manual";
    });

    const unresolved =
        Array.isArray(
            canonicalPayload?.unresolved_relationships
        )
            ? canonicalPayload.unresolved_relationships
            : [];

    unresolved.forEach(item => {
        const node = topologyTreeResolveNode({
            objectKind: item.object_kind,
            objectId: item.id,
            ref: item.ref
        }, canonicalNodes, nodes, ipNodes);

        if (!node || node.parent) {
            return;
        }

        node.relationship = {
            source: "unknown",
            confidence: 0,
            locked: false,
            reason:
                "No canonical relationship was resolved.",
            resolution_status:
                item.resolution_status || "no_evidence",
            diagnostics: Array.isArray(
                item.resolution_diagnostics
            )
                ? item.resolution_diagnostics
                : []
        };
    });

    return diagnostics;
}


function topologyTreeIdentifyCoreServices(
    inventory,
    canonicalNodes,
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
                {
                    objectKind: "device",
                    objectId: device.id,
                    ref: topologyTreeDeviceRef(device)
                },
                canonicalNodes,
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

        unresolved_relationships:
            tree.unresolvedCanonical.length,

        relationship_api_available:
            tree.relationshipsAvailable,

        ingestion_diagnostics:
            tree.ingestionDiagnostics.length
    };
}


function buildTopologyTree(
    inventory = [],
    infrastructureInventory = [],
    canonicalPayload = null
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
    const canonicalNodes = new Map();
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

        if (node.id) {
            canonicalNodes.set(node.id, node);
        }

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

        if (node.id) {
            canonicalNodes.set(node.id, node);
        }

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
                {
                    objectKind: "device",
                    objectId: routers[0].id,
                    ref: topologyTreeDeviceRef(
                        routers[0]
                    )
                },
                canonicalNodes,
                nodes,
                ipNodes
            )
            : null;


    const relationshipsAvailable =
        canonicalPayload?.available === true &&
        Array.isArray(
            canonicalPayload.relationships
        ) &&
        Array.isArray(
            canonicalPayload.unresolved_relationships
        );

    const ingestionDiagnostics =
        relationshipsAvailable
            ? topologyTreeApplyCanonicalRelationships(
                canonicalPayload,
                canonicalNodes,
                nodes,
                ipNodes
            )
            : [{
                code: "canonical_relationships_unavailable"
            }];

    if (!relationshipsAvailable) {
        nodes.forEach(node => {
            node.relationship.resolution_status =
                "unavailable";
        });
    }


    const coreServices =
        topologyTreeIdentifyCoreServices(
            safeInventory,
            canonicalNodes,
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
        canonicalNodes,
        ipNodes,

        coreServices,
        unassigned,

        unresolvedCanonical:
            relationshipsAvailable
                ? canonicalPayload
                    .unresolved_relationships
                : [],

        relationshipsAvailable,
        ingestionDiagnostics,


        getNode(value) {
            return (
                topologyTreeResolveNode(
                    value,
                    canonicalNodes,
                    nodes,
                    ipNodes
                )
            );
        },


        pathTo(value) {
            return topologyTreeBuildPath(
                topologyTreeResolveNode(
                    value,
                    canonicalNodes,
                    nodes,
                    ipNodes
                )
            );
        },


        descendantsOf(value) {
            return topologyTreeDescendants(
                topologyTreeResolveNode(
                    value,
                    canonicalNodes,
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
