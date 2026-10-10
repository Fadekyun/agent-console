// Render backend diagnostics identically in desktop and mobile skill cards.
export function skillToolDiagnostic(value, provider = {}) {
  const wrapper = document.createElement('div');
  wrapper.style.minWidth = '0';
  wrapper.style.maxWidth = '100%';
  wrapper.style.overflowWrap = 'anywhere';
  const badge = document.createElement('span');
  const uncertain = value.version_state && !['verified', 'legacy-compatible'].includes(value.version_state);
  const discovery = provider.discovery || {};
  const healthy = value.linked && (!value.state || value.state === 'present')
    && (!value.source_state || value.source_state === 'present')
    && !value.collision && !value.shadowed && !uncertain && discovery.status !== 'uncertain';
  badge.className = `skill-tool-badge ${healthy ? 'linked' : 'missing'}`;
  const states = { present: 'synced', missing: 'missing', 'wrong-target': 'wrong target', collision: 'path conflict', 'revoked-managed-link': 'no longer allowed' };
  const state = states[value.state] || value.state || (value.linked ? 'synced' : 'missing');
  const flags = [
    value.shadowed ? 'shadowed' : value.collision ? 'duplicate source' : null,
    uncertain ? (value.version_state === 'missing-binary' ? 'not installed' : 'version unverified') : null,
    discovery.status === 'uncertain' ? 'discovery uncertain' : null,
  ];
  badge.textContent = `${value.tool}: ${[state, ...flags].filter(Boolean).join(' · ')}`;
  wrapper.append(badge);
  if (value.version_state || value.expected_source || value.materialized_path || discovery.status) {
    const details = document.createElement('details');
    const summary = document.createElement('summary');
    summary.textContent = `${value.tool} diagnostics`;
    const body = document.createElement('div');
    const lines = [
      value.native_id && `Native skill ID: ${value.native_id}`,
      value.version_state && `Version: ${value.installed_version || 'unknown'} (${value.version_state})`,
      value.source_state && `Source: ${value.source_state}`,
      value.expected_source && `Expected source: ${value.expected_source}`,
      value.materialized_path && `Native path: ${value.materialized_path}`,
      value.target && `Resolved target: ${value.target}`,
      ...(discovery.uncertainty_reasons || []).map((reason) => `Discovery: ${reason}`),
      ...(value.shadowed_by || []).map((path) => `Shadowed by: ${path}`),
      ...(value.discovered_paths || []).map((path) => `Discovered source: ${path}`),
    ];
    for (const line of lines.filter(Boolean)) {
      const item = document.createElement('div');
      item.textContent = line;
      body.append(item);
    }
    details.append(summary, body);
    wrapper.append(details);
  }
  return wrapper;
}

export function skillActionMessage(action, result) {
  const notes = [
    ...(result.problems || []),
    ...(result.skipped || []).map((item) => `${item.tool}: ${item.reason}`),
    ...(result.warnings || []),
  ].join('; ');
  if (!result.ok) return `${action === 'sync' ? 'Sync needs attention' : 'Doctor'}: ${notes || 'See skill diagnostics.'}`;
  const success = action === 'sync' ? `Sync complete: ${result.skills} skills checked` : `Doctor OK (${result.skills} skills)`;
  return notes ? `${success}. ${notes}` : success;
}
