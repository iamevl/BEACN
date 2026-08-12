const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');
const vm = require('node:vm');

const TREE_SOURCE = fs.readFileSync(
  'console/static/js/topology-tree.js',
  'utf8'
);
const VIEW_SOURCE = fs.readFileSync(
  'console/static/js/topology-view-model.js',
  'utf8'
);

function device(ip, deviceType, extra = {}) {
  return {
    id: `uuid-${ip}`,
    ip,
    hostname: `host-${ip}`,
    display_name: '',
    device_type: deviceType,
    is_online: 1,
    connection_method: 'automatic',
    connection_source: 'inferred',
    ...extra,
  };
}

function infrastructure(id, type, parentRef = '', method = 'wired') {
  return {
    id,
    ref: `infra:${id}`,
    name: `Example ${id}`,
    infrastructure_type: type,
    parent_ref: parentRef,
    connection_method: method,
    interfaces: [],
  };
}

function environment(inventory = [], infra = []) {
  const context = {
    window: {},
    devices: inventory,
    infrastructure: infra,
    topologySortDevices(items) {
      return [...items].sort((a, b) => a.ip.localeCompare(b.ip));
    },
    topologySyntheticRouter() {
      return device('', 'router', {synthetic: true});
    },
    topologyDeviceName(item) {
      return item.display_name || item.hostname || item.ip;
    },
    deviceTypeDetails(type) {
      return {label: type, colour: '#000000'};
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

test('configured infrastructure hierarchy is authoritative', () => {
  const router = device('192.0.2.1', 'router');
  const internet = infrastructure('internet', 'internet', '', 'virtual');
  const gateway = infrastructure('gateway', 'isp_gateway', internet.ref);
  const distribution = infrastructure('distribution', 'switch', `device:${router.ip}`);
  const context = environment([router], [internet, gateway, distribution]);
  const tree = context.window.buildTopologyTree(
    context.devices,
    context.infrastructure
  );

  assert.equal(tree.root.ref, internet.ref);
  assert.equal(tree.getNode(gateway.ref).parent.ref, internet.ref);
  assert.equal(tree.getNode(distribution.ref).parent.ref, `device:${router.ip}`);
  assert.equal(tree.getNode(distribution.ref).relationship.source, 'manual');
  assert.equal(tree.getNode(distribution.ref).relationship.confidence, 100);
  assert.equal(tree.getNode(distribution.ref).relationship.locked, true);
});

test('manual device parent and transport are authoritative', () => {
  const router = device('192.0.2.1', 'router');
  const client = device('192.0.2.10', 'phone', {
    connection_method: 'wireless',
    connection_source: 'manual',
    connection_parent_ref: `device:${router.ip}`,
  });
  const context = environment([router, client]);
  const tree = context.window.buildTopologyTree(context.devices, []);
  const node = tree.getNode(`device:${client.ip}`);

  assert.equal(node.parent.ref, `device:${router.ip}`);
  assert.equal(node.transport, 'wireless');
  assert.equal(node.relationship.confidence, 100);
  assert.equal(node.relationship.locked, true);
});

test('single distribution switch inference freezes current roles', () => {
  const router = device('192.0.2.1', 'router');
  const distribution = infrastructure('distribution', 'switch', `device:${router.ip}`);
  const childSwitch = device('192.0.2.20', 'switch');
  const nas = device('192.0.2.21', 'nas');
  const phone = device('192.0.2.22', 'phone');
  const context = environment(
    [router, childSwitch, nas, phone],
    [distribution]
  );
  const tree = context.window.buildTopologyTree(context.devices, context.infrastructure);

  assert.equal(tree.getNode(`device:${childSwitch.ip}`).parent.ref, distribution.ref);
  assert.equal(tree.getNode(`device:${childSwitch.ip}`).relationship.confidence, 70);
  assert.equal(tree.getNode(`device:${nas.ip}`).parent.ref, distribution.ref);
  assert.equal(tree.getNode(`device:${nas.ip}`).relationship.confidence, 65);
  assert.equal(tree.getNode(`device:${phone.ip}`).parent, null);
  assert.deepEqual(
    Array.from(tree.unassigned, node => node.ref),
    [`device:${phone.ip}`]
  );
});

test('view model separates direct wired, wireless, WAP and unassigned branches', () => {
  const router = device('192.0.2.1', 'router');
  const wired = device('192.0.2.10', 'computer', {
    connection_source: 'manual',
    connection_method: 'wired',
    connection_parent_ref: `device:${router.ip}`,
  });
  const wireless = device('192.0.2.11', 'phone', {
    connection_source: 'manual',
    connection_method: 'wireless',
    connection_parent_ref: `device:${router.ip}`,
  });
  const wap = device('192.0.2.12', 'access_point', {
    connection_source: 'manual',
    connection_method: 'wired',
    connection_parent_ref: `device:${router.ip}`,
  });
  const wapClient = device('192.0.2.13', 'tablet', {
    connection_source: 'manual',
    connection_method: 'wireless',
    connection_parent_ref: `device:${wap.ip}`,
  });
  const unknown = device('192.0.2.14', 'speaker');
  const context = environment([router, wired, wireless, wap, wapClient, unknown]);
  const model = context.buildTopologyModel();

  assert.deepEqual(Array.from(model.columns[0].direct, item => item.ip), [wired.ip]);
  assert.deepEqual(Array.from(model.columns[1].direct, item => item.ip), [wireless.ip]);
  const wapColumn = model.columns.find(column => column.kind === 'access_point');
  assert.equal(wapColumn.device.ip, wap.ip);
  assert.deepEqual(Array.from(wapColumn.clients, item => item.ip), [wapClient.ip]);
  assert.deepEqual(Array.from(model.unassigned, item => item.ip), [unknown.ip]);
  const placed = [
    ...model.columns.flatMap(column => column.direct || []),
    ...wapColumn.clients,
    ...model.unassigned,
  ];
  assert.equal(new Set(placed.map(item => item.ip)).size, placed.length);
});

test('infrastructure can nest beneath infrastructure', () => {
  const router = device('192.0.2.1', 'router');
  const parent = infrastructure('parent', 'switch', `device:${router.ip}`);
  const child = infrastructure('child', 'switch', parent.ref);
  const context = environment([router], [parent, child]);
  const tree = context.window.buildTopologyTree(context.devices, context.infrastructure);

  assert.equal(tree.getNode(child.ref).parent.ref, parent.ref);
  assert.deepEqual(
    Array.from(tree.getNode(parent.ref).children, node => node.ref),
    [child.ref]
  );
});

test('missing parent and unknown transport fail into unresolved manual diagnostics', () => {
  const router = device('192.0.2.1', 'router');
  const missing = device('192.0.2.10', 'phone', {
    connection_source: 'manual',
    connection_method: 'wired',
    connection_parent_ref: 'infra:deleted',
  });
  const unknownTransport = device('192.0.2.11', 'phone', {
    connection_source: 'manual',
    connection_method: 'bluetooth',
    connection_parent_ref: `device:${router.ip}`,
  });
  const context = environment([router, missing, unknownTransport]);
  const tree = context.window.buildTopologyTree(context.devices, []);

  assert.equal(tree.unresolvedManual.length, 2);
  assert.equal(tree.getNode(`device:${missing.ip}`).parent, null);
  assert.equal(tree.getNode(`device:${unknownTransport.ip}`).parent, null);
});

test('browser relationship construction rejects manual cycles', () => {
  const first = device('192.0.2.70', 'switch', {
    connection_source: 'manual',
    connection_method: 'wired',
    connection_parent_ref: 'device:192.0.2.71',
  });
  const second = device('192.0.2.71', 'switch', {
    connection_source: 'manual',
    connection_method: 'wired',
    connection_parent_ref: 'device:192.0.2.70',
  });
  const context = environment([first, second]);
  const tree = context.window.buildTopologyTree(context.devices, []);

  assert.equal(tree.getNode(`device:${first.ip}`).parent.ref, `device:${second.ip}`);
  assert.equal(tree.getNode(`device:${second.ip}`).parent, null);
  assert.equal(tree.unresolvedManual.length, 1);
  assert.match(tree.unresolvedManual[0].reason, /cycle/);
});

test('device refs are stable by IP even when canonical UUIDs differ', () => {
  const first = environment([device('192.0.2.40', 'phone', {id: 'uuid-one'})]);
  const second = environment([device('192.0.2.40', 'phone', {id: 'uuid-two'})]);

  assert.equal(
    first.window.buildTopologyTree(first.devices, []).nodes.keys().next().value,
    second.window.buildTopologyTree(second.devices, []).nodes.keys().next().value
  );
  assert.equal(
    first.window.buildTopologyTree(first.devices, []).nodes.keys().next().value,
    'device:192.0.2.40'
  );
});
