// All library text uses textContent; imported packages are never rendered as HTML.
export function setupSkills({ api, el, message, profiles }) {
  const root = document.querySelector('#skills-content');
  let selected = null, loading = false;
  function button(label, action) {
    const node = el('button', label);
    node.onclick = async () => {
      if (loading) return;
      loading = true; root.querySelectorAll('button').forEach(b => b.disabled = true);
      try { const result = await action(); message(typeof result === 'string' ? result : 'Skills updated. Existing session copies are unchanged.'); await load(); }
      catch (error) { message(error.message); }
      finally { loading = false; root.querySelectorAll('button').forEach(b => b.disabled = false); }
    };
    return node;
  }
  function detail(packageInfo, imported = null) {
    const panel = el('section', null, 'panel');
    panel.append(el('h2', packageInfo.name), el('p', packageInfo.description), el('p', `${imported ? imported.status : packageInfo.trust} · ${packageInfo.scope} · ${packageInfo.risk} risk · ${packageInfo.approval}`));
    const identity = el('dl', null, 'skill-identity');
    for (const [label, value] of [['Revision', packageInfo.revision], ['Content hash', packageInfo.hash], ['Source', packageInfo.source], ['Harnesses', packageInfo.compatible_harnesses.join(', ') || 'Any'], ['Roles', packageInfo.compatible_profiles.join(', ') || 'Any'], ['Files', packageInfo.files.join(', ')]]) identity.append(el('dt', label), el('dd', value || 'Unknown'));
    panel.append(identity);
    if (packageInfo.provenance) {
      const p = packageInfo.provenance;
      panel.append(el('p', `Imported from ${p.kind}${p.subdirectory ? ` · ${p.subdirectory}` : ''}${p.requested_revision ? ` · requested ${p.requested_revision}` : ''}`, 'small'));
      if (!packageInfo.provenance_content_matches) panel.append(el('p', 'Content differs from the imported revision. Inspect and review the current package.', 'small'));
      if (packageInfo.declared_source) panel.append(el('p', `Package-declared source: ${packageInfo.declared_source}`, 'small'));
      if (packageInfo.declared_revision) panel.append(el('p', `Package-declared revision: ${packageInfo.declared_revision}`, 'small'));
    }
    [...packageInfo.issues, ...packageInfo.warnings].forEach(text => panel.append(el('p', text, 'small')));
    const services = el('label', 'I verified the declared service dependencies for this revision.', 'check'), verified = el('input'); verified.type = 'checkbox'; services.prepend(verified);
    if (packageInfo.required_services.length) panel.append(services);
    const decision = choice => ({ expected_hash: packageInfo.hash, decision: choice, services_verified: verified.checked });
    const actions = el('div', null, 'actions');
    if (imported) {
      panel.append(el('p', imported.staged_path, 'skill-path'));
      actions.append(button('Review & activate this revision', () => api(`/api/skill-registry/imports/${encodeURIComponent(imported.id)}/activate`, decision('reviewed'))));
      panel.append(el('p', 'Inspect the package files before activation. Scripts are never run by import or activation.', 'small muted'));
    } else {
      actions.append(button('Mark reviewed', () => api(`/api/skill-registry/${packageInfo.name}/review`, decision('reviewed'))), button('Block future delivery', () => api(`/api/skill-registry/${packageInfo.name}/review`, decision('blocked'))));
      const label = el('label', 'Role'), role = el('select');
      profiles().filter(p => p.status !== 'deprecated').forEach(p => { const option = el('option', p.display_name || p.name); option.value = p.name; role.append(option); });
      role.value = 'coder'; label.append(role); panel.append(label);
      actions.append(button('Assign to role', () => api('/api/skills/assign', { profile: role.value, skill_name: packageInfo.name })), button('Remove assignment', () => api('/api/skills/unassign', { profile: role.value, skill_name: packageInfo.name })));
      if (packageInfo.approval === 'ask') actions.append(button('Approve revision for role', () => api(`/api/skill-registry/${packageInfo.name}/approve`, { profile: role.value, expected_hash: packageInfo.hash })), button('Revoke role approval', () => api(`/api/skill-registry/${packageInfo.name}/revoke`, { profile: role.value, expected_hash: packageInfo.hash })));
    }
    panel.append(actions); return panel;
  }
  async function load() {
    const catalog = await api('/api/skill-registry');
    root.replaceChildren();
    const toolbar = el('div', null, 'panel');
    toolbar.append(el('p', 'Review skills here, then preview the effective set when starting a session. Changes take effect on a new session or an explicit restart.'));
    const source = el('input'); source.placeholder = 'Workspace package directory or Git HTTPS URL'; source.setAttribute('aria-label', 'Skill import source');
    const gitOptions = el('details'), gitLabel = el('summary', 'Git revision and package directory');
    const revision = el('input'); revision.value = 'HEAD'; revision.setAttribute('aria-label', 'Git revision');
    const subdirectory = el('input'); subdirectory.placeholder = 'Package directory within repository'; subdirectory.setAttribute('aria-label', 'Git package directory');
    gitOptions.append(gitLabel, revision, subdirectory, el('p', 'Anonymous HTTPS fetch only. Import stages files for inspection; it does not activate or execute them.', 'small'));
    toolbar.append(source, gitOptions, button('Stage import', () => api('/api/skill-registry/imports', { source: source.value, ... (source.value.includes('://') ? { revision: revision.value, subdirectory: subdirectory.value } : {}) })), button('Validate discovery', async () => { const result = await api('/api/skills/doctor', {}); return [...result.problems, ...result.warnings].join('\n') || 'Discovery checks passed.'; }), button('Sync unrestricted skills', () => api('/api/skills/sync', {})));
    root.append(toolbar);
    const cards = el('div', null, 'cards'), details = el('div');
    for (const packageInfo of catalog.entries) {
      const card = el('article', null, 'card');
      const inspect = el('button', 'Inspect'); inspect.onclick = () => { selected = packageInfo.name; details.replaceChildren(detail(packageInfo)); details.scrollIntoView({ block: 'nearest' }); };
      card.append(el('h2', packageInfo.name), el('p', packageInfo.description, 'brief muted'), el('p', `${packageInfo.trust} · ${packageInfo.validation}`), inspect); cards.append(card);
      if (selected === packageInfo.name) details.replaceChildren(detail(packageInfo));
    }
    root.append(cards, details);
    const pending = catalog.imports.filter(item => item.status !== 'activated');
    if (pending.length) root.append(el('h2', 'Pending imports'));
    for (const imported of pending) {
      const item = el('div', null, 'panel');
      const inspect = el('button', 'Inspect staged revision'); inspect.onclick = async () => { try { const data = await api(`/api/skill-registry/imports/${encodeURIComponent(imported.id)}`); selected = null; details.replaceChildren(detail(data.package, data)); } catch (error) { message(error.message); } };
      item.append(el('p', `${imported.name} · ${imported.status}`), inspect); root.append(item);
    }
  }
  return { load };
}
