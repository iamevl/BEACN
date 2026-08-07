"use strict";

/*
 * ------------------------------------------------------------------
 * Topology Tree -> Existing Renderer View Model
 *
 * Relationship truth comes from topology-tree.js.
 *
 * This adapter deliberately preserves the current four-column
 * presentation so the renderer can be migrated independently from
 * the underlying relationship engine.
 *
 * It performs NO database writes and NO relationship inference.
 * ------------------------------------------------------------------
 */

function topologyTreeEndpointChildren(node) {
    if (!node) {
        return [];
    }

    return node.children
        .filter(child =>
            !child.flags.infrastructure
        )
        .map(child => child.device);
}


function topologyTreeInfrastructureChildren(node) {
    if (!node) {
        return [];
    }

    return node.children.filter(child =>
        child.flags.infrastructure
    );
}


function topologyTreeToViewModel(tree) {
    if (!tree) {
        throw new Error(
            'Topology tree is required.'
        );
    }

    const inventory = Array.isArray(devices)
        ? devices
        : [];

    const primaryRouter =
        tree.root?.device ||
        topologySyntheticRouter();

    const coreServiceIps = new Set(
        (tree.coreServices || []).map(service =>
            service.node.ip
        )
    );

    /*
     * Infrastructure nodes are displayed separately from endpoint
     * children in the existing topology UI.
     */
    const switchNodes =
        [...tree.nodes.values()]
            .filter(node =>
                node.device?.device_type === 'switch'
            );

    const accessPointNodes =
        [...tree.nodes.values()]
            .filter(node =>
                node.device?.device_type ===
                'access_point'
            );

    /*
     * Wired infrastructure branches.
     *
     * Their endpoint membership comes directly from node.children.
     * Nested infrastructure such as an AP beneath a switch is NOT
     * incorrectly rendered as an endpoint of that switch.
     */
    const wiredSwitches =
        switchNodes.map(node => ({
            device: node.device,

            clients: topologySortDevices(
                topologyTreeEndpointChildren(node)
            ),

            treeNode: node,

            parentIp:
                node.parent?.ip || '',

            connectionMethod:
                node.transport,

            relationshipSource:
                node.relationship.source
        }));

    /*
     * Direct router-connected endpoint devices.
     */
    const wiredDirect = [];
    const wirelessDirect = [];

    if (tree.root) {
        tree.root.children.forEach(child => {
            if (
                child.flags.infrastructure ||
                coreServiceIps.has(child.ip)
            ) {
                return;
            }

            if (child.transport === 'wired') {
                wiredDirect.push(child.device);
                return;
            }

            if (child.transport === 'wireless') {
                wirelessDirect.push(child.device);
            }
        });
    }

    /*
     * Access points remain horizontal columns in the current UI,
     * regardless of their physical depth in the canonical tree.
     *
     * Their physical parent and transport now come exclusively
     * from the Relationship Engine.
     */
    const accessPointColumns =
        accessPointNodes
            .map(node => {
                const parentNode = node.parent;

                const wirelessBackhaul =
                    node.transport === 'wireless' &&
                    parentNode?.device?.device_type ===
                        'access_point';

                return {
                    key:
                        `access-point-${node.ip}`,

                    kind: 'access_point',

                    label:
                        topologyDeviceName(
                            node.device
                        ),

                    icon: '📡',

                    colour:
                        deviceTypeDetails(
                            'access_point'
                        ).colour,

                    device: node.device,

                    infrastructure:
                        topologyTreeInfrastructureChildren(
                            node
                        ),

                    direct: [],

                    clients:
                        topologySortDevices(
                            topologyTreeEndpointChildren(
                                node
                            )
                        ),

                    connectionMethod:
                        node.transport,

                    parentIp:
                        parentNode?.ip || '',

                    parentDevice:
                        parentNode?.device || null,

                    wirelessBackhaul,

                    relationshipSource:
                        node.relationship.source,

                    relationshipConfidence:
                        node.relationship.confidence,

                    treeNode: node
                };
            })
            .sort((left, right) => {
                /*
                 * Keep a parent AP immediately before its child AP.
                 */
                if (
                    right.parentIp ===
                    left.device.ip
                ) {
                    return -1;
                }

                if (
                    left.parentIp ===
                    right.device.ip
                ) {
                    return 1;
                }

                /*
                 * Ethernet/root APs precede wireless-backhaul APs.
                 */
                const leftRank =
                    left.wirelessBackhaul
                        ? 1
                        : 0;

                const rightRank =
                    right.wirelessBackhaul
                        ? 1
                        : 0;

                if (leftRank !== rightRank) {
                    return leftRank - rightRank;
                }

                return left.label.localeCompare(
                    right.label
                );
            });

    const columns = [
        {
            key: 'wired',
            kind: 'wired',
            label: 'Wired',
            icon: '🔌',
            colour: '#22d3ee',
            device: null,
            infrastructure: wiredSwitches,
            direct:
                topologySortDevices(
                    wiredDirect
                ),
            clients: []
        },
        {
            key: 'wireless',
            kind: 'wireless',
            label: 'Wireless',
            icon: '📶',
            colour: '#a78bfa',
            device: null,
            infrastructure: [],
            direct:
                topologySortDevices(
                    wirelessDirect
                ),
            clients: []
        },
        ...accessPointColumns
    ];

    const coreServices =
        topologySortDevices(
            (tree.coreServices || [])
                .map(service =>
                    service.node.device
                )
        );

    const unassigned =
        topologySortDevices(
            (tree.unassigned || [])
                .map(node => node.device)
        );

    return {
        total: inventory.length,

        online:
            inventory.filter(
                device => device.is_online
            ).length,

        primaryRouter,

        columns,

        coreServices,

        unassigned,

        /*
         * Temporary migration/debug information.
         * The existing renderer ignores this.
         */
        tree,

        diagnostics:
            tree.diagnostics || null,

        modelSource:
            'relationship-engine'
    };
}


function buildTopologyModel() {
    if (
        typeof buildTopologyTree !==
        'function'
    ) {
        throw new Error(
            'Relationship Engine is not loaded.'
        );
    }

    const tree =
        buildTopologyTree(
            Array.isArray(devices)
                ? devices
                : []
        );

    return topologyTreeToViewModel(tree);
}
