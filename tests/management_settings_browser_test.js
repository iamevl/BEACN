const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const test = require('node:test');

const ROOT = path.resolve(__dirname, '..');
const CONTROLLER = fs.readFileSync(
  path.join(ROOT, 'console/static/js/management-settings.js'),
  'utf8'
).replaceAll('</script', '<\\/script');

const source = {
  id: '11111111-1111-4111-8111-111111111111',
  participant_kind: 'device',
  participant_id: '22222222-2222-4222-8222-222222222222',
  adapter_type: 'ssh',
  management_address: '192.0.2.200',
  management_port: 22,
  enabled: true,
  credential_id: '33333333-3333-4333-8333-333333333333',
  connection_timeout_seconds: 5,
  capabilities: {
    interface_inventory: false,
    bridge_fdb: false,
    wireless_associations: false,
    neighbours: false,
  },
  credential: {label: 'Asus Router SSH', credential_type: 'username_password'},
  ssh_trusted: true,
  ssh_host_key_algorithm: 'ssh-ed25519',
  ssh_host_key_fingerprint: 'SHA256:synthetic',
  ssh_host_key_trusted_at: '2026-08-13T15:16:09Z',
  interface_inventory: null,
};

function page(scenario) {
  const responses = JSON.stringify({source});
  return `<!doctype html><html><body>
    <div id="management-sources"></div><div id="management-status"></div>
    <form id="management-credential-form"><input name="credential_id"><input name="label"><select name="credential_type"><option value="username_password">Password</option></select></form>
    <div id="management-secret-fields"></div><div id="management-credential-list"></div><details id="management-credential-editor"></details>
    <button id="management-credential-cancel"></button>
    <div id="management-source-form-title"></div><div id="management-source-form-state"></div>
    <form id="management-source-form">
      <input name="source_id"><select name="participant" required></select>
      <select name="adapter_type" required><option value="ssh">SSH</option></select>
      <input name="management_address" required><input name="management_port" type="number" min="1" max="65535">
      <select name="credential_id"><option value="">None</option></select>
      <input name="connection_timeout_seconds" type="number" min="1" max="30" required>
      <input name="enabled" type="checkbox">
      <input name="capability" type="checkbox" value="interface_inventory">
      <input name="capability" type="checkbox" value="bridge_fdb">
      <input name="capability" type="checkbox" value="wireless_associations">
      <input name="capability" type="checkbox" value="neighbours">
      <button type="submit">Save source</button>
    </form>
    <button id="management-source-cancel"></button><div id="management-source-list"></div>
    <pre id="result"></pre>
    <script>
      const fixture = ${responses};
      window.requests = [];
      window.fetch = async (url, options = {}) => {
        window.requests.push({url, method: options.method || 'GET', body: options.body || null});
        if (url.includes('/api/management/sources/') && options.method === 'PATCH') {
          if (${JSON.stringify(scenario)} === 'network-failure') throw new Error('offline');
          if (${JSON.stringify(scenario)} === 'unexpected-response') return {ok:true,status:200,json:async()=>{throw new Error('not json')}};
          if (${JSON.stringify(scenario)} === 'api-failure') return {ok:false,status:403,json:async()=>({ok:false,error:{message:'CSRF verification failed.'}})};
          return {ok:true,status:200,json:async()=>({ok:true,source:fixture.source})};
        }
        const body = url.endsWith('/csrf') ? {ok:true,csrf_token:'token'}
          : url.endsWith('/devices') ? {devices:[{id:fixture.source.participant_id,hostname:'Synthetic Router',ip:'192.0.2.200'}]}
          : url.endsWith('/infrastructure') ? {infrastructure:[]}
          : url.endsWith('/status') ? {ok:true,encryption_available:true}
          : url.endsWith('/credentials') ? {ok:true,credentials:[{id:fixture.source.credential_id,label:'Asus Router SSH',credential_type:'username_password'}]}
          : url.endsWith('/sources') ? {ok:true,sources:[fixture.source]}
          : {ok:true};
        return {ok:true,status:200,json:async()=>body};
      };
    </script>
    <script>${CONTROLLER}</script>
    <script>
      const runScenario = () => {
        const edit = [...document.querySelectorAll('button')].find(button => button.textContent === 'Edit');
        if (!edit) {
          queueMicrotask(runScenario);
          return;
        }
        edit.click();
        const form = document.getElementById('management-source-form');
        form.elements.management_address.value = '';
        form.elements.source_id.value = '';
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const populated = {
            address: form.elements.management_address.value,
            port: form.elements.management_port.value,
            credential: form.elements.credential_id.value,
            timeout: form.elements.connection_timeout_seconds.value,
            enabled: form.elements.enabled.checked,
            capabilities: [...form.querySelectorAll('[name=capability]')].map(x => [x.value,x.checked]),
          };
          if (${JSON.stringify(scenario)} === 'validation-failure') {
            form.elements.management_address.value = '';
          } else {
            form.querySelector('[value=interface_inventory]').checked = true;
          }
          const statusElement = document.getElementById('management-status');
          const record = (allowPending = false) => {
            if (!allowPending && /^(Saving|Creating)/.test(statusElement.textContent)) return;
            const patch = window.requests.find(request => request.method === 'PATCH') || null;
            document.getElementById('result').textContent = JSON.stringify({
              populated, patch, status: statusElement.textContent,
            });
          };
          new MutationObserver(record).observe(statusElement, {childList: true, subtree: true});
          form.requestSubmit();
          record(true);
        }));
      };
      queueMicrotask(runScenario);
    </script>
  </body></html>`;
}

function run(scenario) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'beacn-management-browser-'));
  const file = path.join(directory, 'fixture.html');
  fs.writeFileSync(file, page(scenario));
  const result = spawnSync('chromium-browser', [
    '--headless', '--no-sandbox', '--disable-gpu', '--disable-software-rasterizer',
    '--disable-background-timer-throttling', '--run-all-compositor-stages-before-draw',
    '--virtual-time-budget=5000', '--dump-dom', `file://${file}`,
  ], {encoding: 'utf8'});
  fs.rmSync(directory, {recursive: true, force: true});
  assert.equal(result.status, 0, result.stderr);
  const match = result.stdout.match(/<pre id="result">([^<]+)<\/pre>/);
  assert.ok(match, result.stdout);
  return JSON.parse(match[1].replaceAll('&quot;', '"').replaceAll('&amp;', '&'));
}

test('persisted Edit survives a delayed browser form rewrite and PATCHes the correct source', () => {
  const result = run('success');
  assert.deepEqual(result.populated, {
    address: '192.0.2.200', port: '22',
    credential: source.credential_id, timeout: '5', enabled: true,
    capabilities: [
      ['interface_inventory', false], ['bridge_fdb', false],
      ['wireless_associations', false], ['neighbours', false],
    ],
  });
  assert.equal(result.patch.url, `/api/management/sources/${source.id}`);
  const payload = JSON.parse(result.patch.body);
  assert.equal(payload.management_address, '192.0.2.200');
  assert.equal(payload.management_port, 22);
  assert.deepEqual(payload.capabilities, {
    interface_inventory: true, bridge_fdb: false,
    wireless_associations: false, neighbours: false,
  });
});

test('native validation failure is visible and sends no request', () => {
  const result = run('validation-failure');
  assert.equal(result.patch, null);
  assert.match(result.status, /invalid or incomplete.*not saved/i);
});

test('API and CSRF rejection remains visibly failed', () => {
  const result = run('api-failure');
  assert.ok(result.patch);
  assert.match(result.status, /CSRF verification failed/i);
  assert.doesNotMatch(result.status, /saved and refreshed/i);
});

test('network failure remains visibly unconfirmed', () => {
  const result = run('network-failure');
  assert.match(result.status, /could not reach the server.*No saved state was confirmed/i);
  assert.doesNotMatch(result.status, /saved and refreshed/i);
});
