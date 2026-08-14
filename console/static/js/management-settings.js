(() => {
  'use strict';

  const root = document.getElementById('management-sources');
  if (!root) return;

  const credentialForm = document.getElementById('management-credential-form');
  const sourceForm = document.getElementById('management-source-form');
  const secretFields = document.getElementById('management-secret-fields');
  const credentialList = document.getElementById('management-credential-list');
  const credentialEditor = document.getElementById('management-credential-editor');
  const sourceList = document.getElementById('management-source-list');
  const sourceFormTitle = document.getElementById('management-source-form-title');
  const sourceFormState = document.getElementById('management-source-form-state');
  const status = document.getElementById('management-status');
  let csrfToken = '';
  let credentials = [];
  let sources = [];
  const participantLabels = new Map();
  const candidates = new Map();

  const fieldDefinitions = {
    username_password: [['username', 'Username', 'text'], ['password', 'Password', 'password']],
    ssh_private_key: [['username', 'Username', 'text'], ['private_key', 'Private key', 'textarea'], ['passphrase', 'Passphrase (optional)', 'password']],
    snmp_v2_community: [['community', 'Community', 'password']],
    snmp_v3: [['username', 'Username', 'text'], ['auth_password', 'Authentication password', 'password'], ['priv_password', 'Privacy password', 'password']],
    api_token: [['token', 'API token', 'password']],
  };

  function element(tag, text, className) {
    const value = document.createElement(tag);
    if (text !== undefined) value.textContent = text;
    if (className) value.className = className;
    return value;
  }

  async function api(path, options = {}) {
    const headers = {'Content-Type': 'application/json'};
    if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = csrfToken;
    const response = await fetch(path, {...options, headers});
    const body = response.status === 204 ? {ok: true} : await response.json();
    if (!response.ok) throw new Error(body?.error?.message || 'Management request failed.');
    return body;
  }

  function show(message, error = false) {
    status.textContent = message;
    status.classList.toggle('warning', error);
  }

  function renderSecretFields() {
    secretFields.replaceChildren();
    const type = credentialForm.elements.credential_type.value;
    for (const [name, labelText, kind] of fieldDefinitions[type] || []) {
      const label = element('label', labelText);
      const input = document.createElement(kind === 'textarea' ? 'textarea' : 'input');
      input.name = name;
      if (kind !== 'textarea') input.type = kind;
      input.required = name !== 'passphrase';
      input.autocomplete = 'new-password';
      label.append(input);
      secretFields.append(label);
    }
  }

  function resetCredentialForm() {
    credentialForm.reset();
    credentialForm.elements.credential_id.value = '';
    credentialForm.elements.label.disabled = false;
    credentialForm.elements.credential_type.disabled = false;
    document.getElementById('management-credential-cancel').hidden = true;
    renderSecretFields();
    credentialEditor.open = false;
  }

  function credentialSecret() {
    const result = {};
    for (const [name] of fieldDefinitions[credentialForm.elements.credential_type.value] || []) {
      const value = credentialForm.elements[name].value;
      if (value || name !== 'passphrase') result[name] = value;
    }
    return result;
  }

  function actionButton(label, action, danger = false) {
    const button = element('button', label, danger ? 'danger' : 'secondary');
    button.type = 'button';
    button.addEventListener('click', action);
    return button;
  }

  function renderCredentials() {
    credentialList.replaceChildren();
    const select = sourceForm.elements.credential_id;
    const selected = select.value;
    select.replaceChildren(new Option('None', ''));
    credentials.forEach(item => {
      select.add(new Option(`${item.label} (${item.credential_type})`, item.id));
      const card = element('div', undefined, 'management-entry management-credential-entry');
      const identity = element('div', undefined, 'management-credential-identity');
      identity.append(element('strong', item.label), element('span', item.credential_type.replaceAll('_', ' '), 'badge'));
      const actions = element('div', undefined, 'settings-actions management-entry-actions');
      actions.append(
        actionButton('Rotate', () => {
          resetCredentialForm();
          credentialForm.elements.credential_id.value = item.id;
          credentialForm.elements.label.value = item.label;
          credentialForm.elements.credential_type.value = item.credential_type;
          credentialForm.elements.label.disabled = true;
          credentialForm.elements.credential_type.disabled = true;
          document.getElementById('management-credential-cancel').hidden = false;
          renderSecretFields();
          credentialEditor.open = true;
          credentialForm.scrollIntoView({behavior: 'smooth'});
        }),
        actionButton('Delete', async () => {
          if (!window.confirm(`Delete credential “${item.label}”?`)) return;
          try {
            await api(`/api/management/credentials/${item.id}`, {method: 'DELETE'});
            await refresh();
          } catch (error) { show(error.message, true); }
        }, true),
      );
      card.append(identity, actions);
      credentialList.append(card);
    });
    select.value = credentials.some(item => item.id === selected) ? selected : '';
  }

  function sourcePayload() {
    const [participant_kind, participant_id] = sourceForm.elements.participant.value.split(':', 2);
    const portValue = sourceForm.elements.management_port.value;
    const capabilities = {};
    sourceForm.querySelectorAll('input[name="capability"]').forEach(input => {
      capabilities[input.value] = input.checked;
    });
    return {
      participant_kind,
      participant_id,
      adapter_type: sourceForm.elements.adapter_type.value,
      management_address: sourceForm.elements.management_address.value,
      management_port: portValue ? Number(portValue) : null,
      enabled: sourceForm.elements.enabled.checked,
      credential_id: sourceForm.elements.credential_id.value || null,
      connection_timeout_seconds: Number(sourceForm.elements.connection_timeout_seconds.value),
      capabilities,
    };
  }

  function resetSourceForm() {
    sourceForm.reset();
    sourceForm.elements.source_id.value = '';
    sourceForm.elements.participant.disabled = false;
    sourceForm.elements.adapter_type.disabled = false;
    sourceForm.elements.connection_timeout_seconds.value = '5';
    document.getElementById('management-source-cancel').hidden = true;
    sourceFormTitle.textContent = 'New source configuration';
    sourceFormState.textContent = 'New';
    sourceFormState.classList.remove('management-form-state-editing');
  }

  function sourceEndpoint(item) {
    return `${item.management_address}:${item.management_port || (item.adapter_type === 'ssh' ? 22 : 161)}`;
  }

  function candidateIsCurrent(item, pending) {
    return Boolean(
      pending
      && pending.sourceId === item.id
      && pending.endpoint === sourceEndpoint(item)
    );
  }

  function invalidateCandidate(sourceId) {
    candidates.delete(sourceId);
  }

  function renderSources() {
    sourceList.replaceChildren();
    sources.forEach(item => {
      if (item.ssh_trusted) invalidateCandidate(item.id);
      const card = element('article', undefined, 'management-entry management-source-entry');
      const participant = participantLabels.get(`${item.participant_kind}:${item.participant_id}`)
        || `${item.participant_kind} · ${item.participant_id}`;
      const credential = item.credential
        ? `${item.credential.label} (${item.credential.credential_type})`
        : 'None';
      const header = element('div', undefined, 'management-source-header');
      const heading = element('div', undefined, 'management-source-heading');
      heading.append(
        element('strong', participant, 'management-source-name'),
        element('code', sourceEndpoint(item), 'management-endpoint'),
      );
      const badges = element('div', undefined, 'management-source-badges');
      badges.append(
        element('span', item.adapter_type.toUpperCase(), 'badge'),
        element('span', item.enabled ? 'Enabled' : 'Disabled', `badge management-state-badge ${item.enabled ? 'management-state-enabled' : ''}`),
      );
      header.append(heading, badges);

      const details = element('dl', undefined, 'management-source-details');
      const detail = (label, value) => {
        const group = element('div');
        group.append(element('dt', label), element('dd', value));
        details.append(group);
      };
      detail('Persisted endpoint', sourceEndpoint(item));
      detail('Credential', credential);
      detail('Timeout', `${item.connection_timeout_seconds} seconds`);
      detail('Explicit management actions', item.enabled ? 'Enabled' : 'Disabled');
      card.append(header, details);

      if (item.adapter_type === 'ssh') {
        const trustPanel = element('div', undefined, `management-trust-state ${item.ssh_trusted ? 'management-trust-state-trusted' : ''}`);
        const trustHeader = element('div', undefined, 'management-trust-heading');
        trustHeader.append(
          element('strong', item.ssh_trusted ? 'Trusted SSH identity' : 'SSH identity not trusted'),
          element('span', item.ssh_trusted ? 'Trusted' : 'Not trusted', 'badge'),
        );
        trustPanel.append(trustHeader);
        if (item.ssh_trusted) {
          const trustMetadata = element('div', undefined, 'management-trust-metadata');
          trustMetadata.append(
            element('span', `Algorithm: ${item.ssh_host_key_algorithm}`),
            element('span', item.ssh_host_key_trusted_at ? `Trusted: ${item.ssh_host_key_trusted_at}` : ''),
          );
          const fingerprint = element('div', undefined, 'management-fingerprint-row');
          fingerprint.append(
            element('span', 'SHA256 fingerprint'),
            element('code', item.ssh_host_key_fingerprint, 'management-fingerprint'),
          );
          trustPanel.append(trustMetadata, fingerprint);
        }
        card.append(trustPanel);
      }
      const result = element('div', undefined, 'management-result');
      const actions = element('div', undefined, 'settings-actions management-entry-actions');
      const review = element('section', undefined, 'management-trust-review');
      review.hidden = true;
      const reviewTitle = element('strong', 'Review SSH host identity');
      const reviewExplanation = element(
        'span',
        'The SSH server presented this host identity. Verify it belongs to the intended device before trusting it.',
      );
      const reviewSource = element('span', undefined, 'management-review-source');
      const reviewAlgorithm = element('code', undefined, 'management-review-algorithm');
      const reviewFingerprint = element('code', undefined, 'management-fingerprint management-review-fingerprint');
      const reviewActions = element('div', undefined, 'settings-actions management-entry-actions');

      const dismiss = actionButton('Cancel / Dismiss', () => {
        invalidateCandidate(item.id);
        review.hidden = true;
      });

      const trust = actionButton('Trust this identity', async () => {
        const pending = candidates.get(item.id);
        const current = sources.find(source => source.id === item.id);
        if (!current || !candidateIsCurrent(current, pending)) {
          invalidateCandidate(item.id);
          review.hidden = true;
          show('The SSH identity candidate is stale. Test the current endpoint again before trusting.', true);
          return;
        }
        if (!window.confirm(`Trust ${pending.identity.algorithm} ${pending.identity.fingerprint} for ${pending.endpoint}?`)) return;
        try {
          await api(`/api/management/sources/${item.id}/trust`, {
            method: 'POST', body: JSON.stringify(pending.identity),
          });
          invalidateCandidate(item.id);
          review.hidden = true;
          await refresh();
        } catch (error) { show(error.message, true); }
      });
      reviewActions.append(trust, dismiss);
      review.append(
        reviewTitle,
        reviewExplanation,
        reviewSource,
        reviewAlgorithm,
        reviewFingerprint,
        reviewActions,
      );

      actions.append(
        actionButton('Edit', () => {
          invalidateCandidate(item.id);
          review.hidden = true;
          resetSourceForm();
          sourceForm.elements.source_id.value = item.id;
          sourceForm.elements.participant.value = `${item.participant_kind}:${item.participant_id}`;
          sourceForm.elements.adapter_type.value = item.adapter_type;
          sourceForm.elements.management_address.value = item.management_address;
          sourceForm.elements.management_port.value = item.management_port || '';
          sourceForm.elements.enabled.checked = item.enabled;
          sourceForm.elements.credential_id.value = item.credential_id || '';
          sourceForm.elements.connection_timeout_seconds.value = item.connection_timeout_seconds;
          sourceForm.querySelectorAll('input[name="capability"]').forEach(input => {
            input.checked = Boolean(item.capabilities[input.value]);
          });
          sourceForm.elements.participant.disabled = true;
          sourceForm.elements.adapter_type.disabled = true;
          document.getElementById('management-source-cancel').hidden = false;
          sourceFormTitle.textContent = `Editing ${participant}`;
          sourceFormState.textContent = 'Editing persisted source';
          sourceFormState.classList.add('management-form-state-editing');
          sourceForm.scrollIntoView({behavior: 'smooth'});
        }),
        actionButton('Test', async () => {
          result.textContent = 'Testing…';
          try {
            const body = await api(`/api/management/sources/${item.id}/test`, {method: 'POST', body: '{}'});
            result.textContent = body.result.message;
            if (
              !item.ssh_trusted
              && body.result.category === 'host_identity_untrusted'
              && body.result.candidate
            ) {
              const pending = {
                sourceId: item.id,
                endpoint: sourceEndpoint(item),
                identity: body.result.candidate,
              };
              candidates.set(item.id, pending);
              reviewSource.textContent = `${participant} · ${pending.endpoint}`;
              reviewAlgorithm.textContent = `Algorithm: ${pending.identity.algorithm}`;
              reviewFingerprint.textContent = `Fingerprint: ${pending.identity.fingerprint}`;
              review.hidden = false;
            } else {
              invalidateCandidate(item.id);
              review.hidden = true;
              if (body.result.category === 'host_identity_changed') {
                result.textContent = 'Security warning: the SSH host identity changed. No trust change was made.';
              }
            }
          } catch (error) { result.textContent = error.message; }
        }),
      );
      actions.append(
        actionButton('Delete', async () => {
          if (!window.confirm('Delete this management source?')) return;
          try {
            await api(`/api/management/sources/${item.id}`, {method: 'DELETE'});
            await refresh();
          } catch (error) { show(error.message, true); }
        }, true),
      );
      card.append(actions, result, review);
      sourceList.append(card);
    });
  }

  async function loadParticipants() {
    const [deviceResponse, infrastructureResponse] = await Promise.all([
      fetch('/api/devices').then(value => value.json()),
      fetch('/api/infrastructure').then(value => value.json()),
    ]);
    const select = sourceForm.elements.participant;
    select.replaceChildren();
    (deviceResponse.devices || []).forEach(item => {
      const label = `Device · ${item.display_name || item.hostname || item.ip}`;
      participantLabels.set(`device:${item.id}`, label);
      select.add(new Option(label, `device:${item.id}`));
    });
    (infrastructureResponse.infrastructure || []).forEach(item => {
      const label = `Infrastructure · ${item.name}`;
      participantLabels.set(`infrastructure_object:${item.id}`, label);
      select.add(new Option(label, `infrastructure_object:${item.id}`));
    });
  }

  async function refresh() {
    const [statusBody, credentialBody, sourceBody] = await Promise.all([
      api('/api/management/status'),
      api('/api/management/credentials'),
      api('/api/management/sources'),
    ]);
    credentials = credentialBody.credentials;
    sources = sourceBody.sources;
    show(statusBody.encryption_available
      ? 'Credential encryption is available.'
      : 'Credential encryption is locked. Provision the root-key file before creating credentials.',
      !statusBody.encryption_available);
    renderCredentials();
    renderSources();
  }

  credentialForm.elements.credential_type.addEventListener('change', renderSecretFields);
  credentialForm.addEventListener('submit', async event => {
    event.preventDefault();
    const id = credentialForm.elements.credential_id.value;
    const secret = credentialSecret();
    try {
      if (id) {
        await api(`/api/management/credentials/${id}`, {method: 'PUT', body: JSON.stringify({secret})});
      } else {
        await api('/api/management/credentials', {
          method: 'POST',
          body: JSON.stringify({
            credential_type: credentialForm.elements.credential_type.value,
            label: credentialForm.elements.label.value,
            secret,
          }),
        });
      }
      resetCredentialForm();
      await refresh();
    } catch (error) { show(error.message, true); }
  });
  sourceForm.addEventListener('submit', async event => {
    event.preventDefault();
    const id = sourceForm.elements.source_id.value;
    const payload = sourcePayload();
    try {
      if (id) {
        invalidateCandidate(id);
        delete payload.participant_kind;
        delete payload.participant_id;
        delete payload.adapter_type;
        await api(`/api/management/sources/${id}`, {method: 'PATCH', body: JSON.stringify(payload)});
      } else {
        await api('/api/management/sources', {method: 'POST', body: JSON.stringify(payload)});
      }
      resetSourceForm();
      await refresh();
    } catch (error) { show(error.message, true); }
  });
  document.getElementById('management-credential-cancel').addEventListener('click', resetCredentialForm);
  document.getElementById('management-source-cancel').addEventListener('click', resetSourceForm);

  (async () => {
    try {
      csrfToken = (await api('/api/management/csrf')).csrf_token;
      await loadParticipants();
      renderSecretFields();
      await refresh();
    } catch (error) { show(error.message, true); }
  })();
})();
