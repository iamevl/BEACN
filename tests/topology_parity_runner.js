"use strict";

const fs = require("node:fs");
const vm = require("node:vm");

const input = JSON.parse(
    fs.readFileSync(0, "utf8")
);

const source = input.tree_source || fs.readFileSync(
    "console/static/js/topology-tree.js",
    "utf8"
);

const context = {
    window: {},
    Array,
    Boolean,
    Map,
    Math,
    Number,
    Set,
    String,
};

vm.createContext(context);
vm.runInContext(source, context);

const tree = context.window.buildTopologyTree(
    input.devices,
    input.infrastructure,
    input.canonical_relationships
);

const identities = new Map();

input.devices.forEach(item => {
    identities.set(`device:${item.ip}`, {
        id: item.id,
        kind: "device",
    });
});

input.infrastructure.forEach(item => {
    identities.set(item.ref || `infra:${item.id}`, {
        id: item.id,
        kind: "infrastructure",
    });
});

function identity(ref) {
    const item = identities.get(ref);

    return item
        ? `${item.kind}:${item.id}`
        : ref;
}

const relationships = [...tree.nodes.values()]
    .filter(node => node.parent)
    .map(node => ({
        subject: identity(node.ref),
        parent: identity(node.parent.ref),
        transport: node.transport || "unknown",
        resolved: true,
    }))
    .sort((left, right) =>
        left.subject.localeCompare(right.subject)
    );

const unresolved = [...tree.nodes.values()]
    .filter(node =>
        node.object_kind === "device" &&
        !node.parent
    )
    .map(node => identity(node.ref))
    .sort();

process.stdout.write(JSON.stringify({
    relationships,
    unresolved,
    node_count: tree.nodes.size,
    unique_node_count:
        new Set(tree.nodes.values()).size,
    ingestion_diagnostics:
        tree.ingestionDiagnostics,
}));
