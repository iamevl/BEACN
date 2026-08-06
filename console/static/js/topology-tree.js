"use strict";

/*
 * ------------------------------------------------------------------
 * BEACN Topology Tree Engine
 *
 * This module owns the canonical relationship graph for the network.
 * It deliberately contains NO rendering logic.
 *
 * Database
 *      ↓
 * Relationship Tree
 *      ↓
 * View Models
 *      ↓
 * Renderer
 *
 * ------------------------------------------------------------------
 */

function topologyCreateNode(device) {
    return {
        device,
        parent: null,
        children: [],
        transport: null,
        source: null
    };
}


function buildTopologyTree(inventory) {

    const nodes = new Map();

    inventory.forEach(device => {
        nodes.set(
            device.ip,
            topologyCreateNode(device)
        );
    });

    let router = null;

    inventory.forEach(device => {
        if (
            !router &&
            device.device_type === "router"
        ) {
            router = nodes.get(device.ip);
        }
    });

    inventory.forEach(device => {

        const node = nodes.get(device.ip);

        const parent =
            nodes.get(device.connection_parent_ip);

        if (
            parent &&
            device.connection_source === "manual"
        ) {
            node.parent = parent;

            node.transport =
                device.connection_method;

            parent.children.push(node);
        }
    });

    return {
        root: router,
        nodes
    };
}

window.buildTopologyTree =
    buildTopologyTree;
