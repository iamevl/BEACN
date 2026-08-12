"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

const TREE_SOURCE = fs.readFileSync(
    "console/static/js/topology-tree.js",
    "utf8"
);
const VIEW_SOURCE = fs.readFileSync(
    "console/static/js/topology-view-model.js",
    "utf8"
);

function device(number, deviceType, extra = {}) {
    const ip = `192.0.2.${number}`;
    return {
        id: `device-${number}`,
        ip,
        hostname: `host-${number}.example.invalid`,
        display_name: `Example Device ${number}`,
        device_type: deviceType,
        is_online: 1,
        ...extra,
    };
}

function infrastructure(id, type) {
    return {
        id,
        ref: `infra:${id}`,
        name: `Example ${id}`,
        infrastructure_type: type,
        interfaces: [],
    };
}

function identity(item) {
    const kind = item.infrastructure_type
        ? "infrastructure"
        : "device";
    const ref = kind === "infrastructure"
        ? item.ref
        : `device:${item.ip}`;
    return {kind, id: item.id, ref};
}

function relationship(subject, parent, transport = "wired", extra = {}) {
    const left = identity(subject);
    const right = identity(parent);
    return {
        resolved: true,
        resolution_status: "resolved",
        subject_id: left.id,
        subject_kind: left.kind,
        subject_ref: left.ref,
        parent_id: right.id,
        parent_kind: right.kind,
        parent_ref: right.ref,
        transport,
        confidence: 100,
        reason: "synthetic_canonical_relationship",
        provider: "manual",
        ...extra,
    };
}

function payload(relationships = [], unresolved = []) {
    return {
        available: true,
        relationships,
        unresolved_relationships: unresolved,
    };
}

function unresolved(item, status) {
    const subject = identity(item);
    return {
        id: subject.id,
        object_kind: subject.kind,
        ref: subject.ref,
        resolved: false,
        resolution_status: status,
        resolution_diagnostics: [{code: status}],
    };
}

function environment(inventory = [], infra = [], canonical = payload()) {
    const context = {
        window: {},
        devices: inventory,
        infrastructure: infra,
        canonicalRelationships: canonical,
        topologySortDevices(items) {
            return [...items].sort((a, b) =>
                a.ip.localeCompare(b.ip)
            );
        },
        topologySyntheticRouter() {
            return {
                id: "synthetic-router",
                ip: "",
                device_type: "router",
                synthetic: true,
            };
        },
        topologyDeviceName(item) {
            return item.display_name || item.hostname || item.ip;
        },
        deviceTypeDetails(type) {
            return {label: type, colour: "#000000"};
        },
        activeDeviceTypeFilter: null,
        Array,
        Boolean,
        Map,
        Math,
        Number,
        Set,
        String,
    };
    vm.createContext(context);
    vm.runInContext(TREE_SOURCE, context);
    vm.runInContext(VIEW_SOURCE, context);
    return context;
}

function tree(context) {
    return context.window.buildTopologyTree(
        context.devices,
        context.infrastructure,
        context.canonicalRelationships
    );
}

test("canonical infrastructure hierarchy supports arbitrary depth", () => {
    const router = device(1, "router");
    const internet = infrastructure("internet", "internet");
    const gateway = infrastructure("gateway", "isp_gateway");
    const distribution = infrastructure("distribution", "switch");
    const access = infrastructure("access", "access_point");
    const relationships = [
        relationship(gateway, internet, "virtual"),
        relationship(router, gateway),
        relationship(distribution, router),
        relationship(access, distribution),
    ];
    const context = environment(
        [router],
        [internet, gateway, distribution, access],
        payload(relationships)
    );
    const result = tree(context);

    assert.equal(result.root.ref, internet.ref);
    assert.equal(result.getNode(access.ref).depth, 4);
    assert.equal(
        result.getNode("infrastructure:access").parent.ref,
        distribution.ref
    );
});

test("manual device to infrastructure uses canonical metadata directly", () => {
    const endpoint = device(10, "nas");
    const parent = infrastructure("switch", "switch");
    const winner = relationship(endpoint, parent, "wired", {
        confidence: 73,
        reason: "server_selected_reason",
        provider: "generic",
    });
    const result = tree(environment(
        [endpoint],
        [parent],
        payload([winner])
    ));
    const node = result.getNode(`device:${endpoint.ip}`);

    assert.equal(node.parent.ref, parent.ref);
    assert.equal(node.transport, "wired");
    assert.equal(node.relationship.source, "generic");
    assert.equal(node.relationship.confidence, 73);
    assert.equal(node.relationship.locked, false);
    assert.equal(node.relationship.reason, "server_selected_reason");
    assert.equal(node.relationship.resolution_status, "resolved");
});

test("direct wired, direct wireless and WAP clients remain presentation branches", () => {
    const router = device(1, "router");
    const wired = device(11, "computer");
    const wireless = device(12, "phone");
    const wap = device(13, "access_point");
    const client = device(14, "tablet");
    const canonical = payload([
        relationship(wired, router, "wired"),
        relationship(wireless, router, "wireless"),
        relationship(wap, router, "wired"),
        relationship(client, wap, "wireless"),
    ]);
    const context = environment(
        [router, wired, wireless, wap, client],
        [],
        canonical
    );
    const model = context.buildTopologyModel();

    assert.deepEqual(
        Array.from(model.columns[0].direct, item => item.id),
        [wired.id]
    );
    assert.deepEqual(
        Array.from(model.columns[1].direct, item => item.id),
        [wireless.id]
    );
    const wapColumn = model.columns.find(
        column => column.kind === "access_point"
    );
    assert.deepEqual(
        Array.from(wapColumn.clients, item => item.id),
        [client.id]
    );
});

test("device role never reconstructs an absent canonical relationship", () => {
    const router = device(1, "router");
    const roles = ["nas", "server", "media_tuner", "ups"];
    const endpoints = roles.map((role, index) =>
        device(20 + index, role, {agent_available: 1})
    );
    const result = tree(environment(
        [router, ...endpoints],
        [],
        payload([], endpoints.map(item =>
            unresolved(item, "no_evidence")
        ))
    ));

    endpoints.forEach(item => {
        const node = result.getNode(`device:${item.ip}`);
        assert.equal(node.parent, null);
        assert.equal(node.relationship.resolution_status, "no_evidence");
    });
});

test("all canonical unresolved statuses remain unattached", () => {
    const statuses = [
        "no_evidence",
        "invalid_manual",
        "invalid_parent",
        "ambiguous",
        "graph_rejected",
    ];
    const endpoints = statuses.map((status, index) =>
        device(30 + index, "phone", {status})
    );
    const result = tree(environment(
        endpoints,
        [],
        payload([], endpoints.map((item, index) =>
            unresolved(item, statuses[index])
        ))
    ));

    endpoints.forEach((item, index) => {
        const node = result.getNode(`device:${item.ip}`);
        assert.equal(node.parent, null);
        assert.equal(
            node.relationship.resolution_status,
            statuses[index]
        );
    });
});

test("missing canonical parent fails honestly without substitution", () => {
    const endpoint = device(40, "nas");
    const absent = infrastructure("absent", "switch");
    const result = tree(environment(
        [endpoint],
        [],
        payload([relationship(endpoint, absent)])
    ));

    assert.equal(result.getNode(`device:${endpoint.ip}`).parent, null);
    assert.equal(result.ingestionDiagnostics.length, 1);
    assert.equal(
        result.ingestionDiagnostics[0].code,
        "canonical_participant_missing"
    );
});

test("canonical UUID lookup wins over an incorrect compatibility ref", () => {
    const first = device(50, "phone");
    const second = device(51, "phone");
    const parent = infrastructure("parent", "switch");
    const winner = relationship(first, parent);
    winner.subject_ref = `device:${second.ip}`;
    const result = tree(environment(
        [first, second],
        [parent],
        payload([winner])
    ));

    assert.equal(result.getNode(`device:${first.ip}`).parent.ref, parent.ref);
    assert.equal(result.getNode(`device:${second.ip}`).parent, null);
});

test("compatibility refs remain a fallback when canonical IDs are absent", () => {
    const endpoint = device(60, "phone");
    const parent = infrastructure("parent", "switch");
    const winner = relationship(endpoint, parent, "wireless");
    delete winner.subject_id;
    delete winner.subject_kind;
    delete winner.parent_id;
    delete winner.parent_kind;
    const result = tree(environment(
        [endpoint],
        [parent],
        payload([winner])
    ));

    assert.equal(result.getNode(`device:${endpoint.ip}`).parent.ref, parent.ref);
    assert.equal(result.getNode(`device:${endpoint.ip}`).transport, "wireless");
});

test("canonical and compatibility aliases never duplicate nodes", () => {
    const endpoint = device(70, "phone");
    const parent = infrastructure("parent", "switch");
    const result = tree(environment(
        [endpoint],
        [parent],
        payload([relationship(endpoint, parent)])
    ));

    assert.equal(result.nodes.size, 2);
    assert.equal(result.canonicalNodes.size, 2);
    assert.equal(
        result.getNode(`device:${endpoint.id}`),
        result.getNode(`device:${endpoint.ip}`)
    );
});

test("empty canonical payload leaves every endpoint unresolved", () => {
    const router = device(1, "router");
    const endpoint = device(80, "nas");
    const result = tree(environment(
        [router, endpoint],
        [],
        payload()
    ));

    assert.equal(result.relationshipsAvailable, true);
    assert.equal(result.getNode(`device:${endpoint.ip}`).parent, null);
});

test("unavailable or malformed relationship data never invokes inference", () => {
    const router = device(1, "router");
    const endpoint = device(90, "nas");

    for (const canonical of [
        {available: false},
        {available: true, relationships: {}},
    ]) {
        const result = tree(environment(
            [router, endpoint],
            [],
            canonical
        ));
        const node = result.getNode(`device:${endpoint.ip}`);
        assert.equal(result.relationshipsAvailable, false);
        assert.equal(node.parent, null);
        assert.equal(node.relationship.resolution_status, "unavailable");
    }
});
