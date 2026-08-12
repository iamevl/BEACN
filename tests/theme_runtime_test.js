const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const ROOT = path.resolve(__dirname, '..');
const THEME_SOURCE = fs.readFileSync(
  path.join(ROOT, 'console/static/js/theme.js'),
  'utf8'
);
const BOOTSTRAP_TEMPLATE = fs.readFileSync(
  path.join(ROOT, 'console/templates/_theme_bootstrap.html'),
  'utf8'
);
const BOOTSTRAP_SOURCE = BOOTSTRAP_TEMPLATE
  .replace(/^<script>\s*/, '')
  .replace(/\s*<\/script>\s*$/, '');

function environment({stored = null, systemDark = true, storageThrows = false} = {}) {
  const root = {dataset: {}, style: {}};
  const meta = {
    attributes: {},
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
  const documentListeners = new Map();
  const windowListeners = new Map();
  const mediaListeners = [];
  const media = {
    matches: systemDark,
    addEventListener(name, listener) {
      if (name === 'change') mediaListeners.push(listener);
    },
    setMatches(value) {
      this.matches = value;
      mediaListeners.forEach(listener => listener({matches: value}));
    },
  };
  const values = new Map();
  if (stored !== null) values.set('beacn.appearance', stored);
  const localStorage = {
    getItem(key) {
      if (storageThrows) throw new Error('storage unavailable');
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      if (storageThrows) throw new Error('storage unavailable');
      values.set(key, value);
    },
  };
  const document = {
    documentElement: root,
    querySelectorAll(selector) {
      return selector === 'meta[name="theme-color"]' ? [meta] : [];
    },
    addEventListener(name, listener) {
      const listeners = documentListeners.get(name) || [];
      listeners.push(listener);
      documentListeners.set(name, listeners);
    },
    dispatchEvent(event) {
      (documentListeners.get(event.type) || []).forEach(listener => listener(event));
      return true;
    },
  };
  const window = {
    localStorage,
    matchMedia(query) {
      assert.equal(query, '(prefers-color-scheme: dark)');
      return media;
    },
    addEventListener(name, listener) {
      const listeners = windowListeners.get(name) || [];
      listeners.push(listener);
      windowListeners.set(name, listeners);
    },
    emitStorage(newValue, key = 'beacn.appearance') {
      (windowListeners.get('storage') || []).forEach(listener => listener({key, newValue}));
    },
  };
  class CustomEvent {
    constructor(type, options) {
      this.type = type;
      this.detail = options.detail;
    }
  }

  return {
    context: vm.createContext({window, document, CustomEvent, Set, Object}),
    document,
    localStorage,
    media,
    meta,
    root,
    values,
    window,
  };
}

function runBootstrap(options) {
  const env = environment(options);
  vm.runInContext(BOOTSTRAP_SOURCE, env.context);
  return env;
}

function runController(options) {
  const env = runBootstrap(options);
  env.events = [];
  env.document.addEventListener('beacn:themechange', event => {
    env.events.push(event.detail);
  });
  vm.runInContext(THEME_SOURCE, env.context);
  return env;
}

test('bootstrap defaults missing and invalid preferences to dark', () => {
  for (const stored of [null, 'invalid']) {
    const env = runBootstrap({stored});
    assert.equal(env.root.dataset.appearance, 'dark');
    assert.equal(env.root.dataset.theme, 'dark');
    assert.equal(env.root.style.colorScheme, 'dark');
    assert.equal(env.meta.attributes.content, '#0F172A');
  }
});

test('bootstrap recognizes explicit dark and light', () => {
  const dark = runBootstrap({stored: 'dark'});
  assert.equal(dark.root.dataset.theme, 'dark');

  const light = runBootstrap({stored: 'light'});
  assert.equal(light.root.dataset.appearance, 'light');
  assert.equal(light.root.dataset.theme, 'light');
  assert.equal(light.meta.attributes.content, '#F8FAFC');
});

test('bootstrap resolves system preference and fails safely', () => {
  assert.equal(
    runBootstrap({stored: 'system', systemDark: true}).root.dataset.theme,
    'dark'
  );
  assert.equal(
    runBootstrap({stored: 'system', systemDark: false}).root.dataset.theme,
    'light'
  );

  const failed = runBootstrap({stored: 'light', storageThrows: true});
  assert.equal(failed.root.dataset.appearance, 'dark');
  assert.equal(failed.root.dataset.theme, 'dark');
});

test('controller accepts only contract preferences and avoids duplicate events', () => {
  const env = runController({stored: 'dark'});
  const theme = env.window.BEACNTheme;

  assert.deepEqual([...theme.appearances], ['dark', 'light', 'system']);
  assert.equal(env.events.length, 0);

  theme.apply('light');
  assert.equal(theme.getAppearance(), 'light');
  assert.equal(theme.getEffectiveTheme(), 'light');
  assert.equal(env.values.get('beacn.appearance'), 'light');
  assert.equal(env.events.length, 1);
  assert.equal(env.events[0].appearance, 'light');
  assert.equal(env.events[0].preference, 'light');
  assert.equal(env.events[0].theme, 'light');
  assert.equal(env.events[0].effectiveTheme, 'light');

  theme.apply('light');
  assert.equal(env.events.length, 1);

  theme.apply('untrusted');
  assert.equal(theme.getAppearance(), 'dark');
  assert.equal(env.values.get('beacn.appearance'), 'dark');
});

test('system follows media changes while explicit modes ignore them', () => {
  const env = runController({stored: 'system', systemDark: true});
  const theme = env.window.BEACNTheme;

  env.media.setMatches(false);
  assert.equal(theme.getEffectiveTheme(), 'light');
  assert.equal(env.events.length, 1);

  theme.apply('dark');
  const explicitEventCount = env.events.length;
  env.media.setMatches(true);
  env.media.setMatches(false);
  assert.equal(theme.getEffectiveTheme(), 'dark');
  assert.equal(env.events.length, explicitEventCount);

  theme.apply('system');
  assert.equal(theme.getEffectiveTheme(), 'light');
  env.media.setMatches(true);
  assert.equal(theme.getEffectiveTheme(), 'dark');
});

test('storage events synchronize valid, missing, and invalid preferences', () => {
  const env = runController({stored: 'dark'});
  const theme = env.window.BEACNTheme;

  env.window.emitStorage('system');
  assert.equal(theme.getAppearance(), 'system');

  env.window.emitStorage('light');
  assert.equal(theme.getAppearance(), 'light');
  assert.equal(theme.getEffectiveTheme(), 'light');

  env.window.emitStorage('invalid');
  assert.equal(theme.getAppearance(), 'dark');

  env.window.emitStorage(null);
  assert.equal(theme.getAppearance(), 'dark');
});

test('controller remains usable when storage is unavailable', () => {
  const env = runController({storageThrows: true});
  const state = env.window.BEACNTheme.apply('light');

  assert.equal(state.appearance, 'light');
  assert.equal(state.effectiveTheme, 'light');
  assert.equal(env.root.dataset.theme, 'light');
});
