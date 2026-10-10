const $ = id => document.getElementById(id);
let generation = 0;
let saving = false;
let renderedScope = null;
const selectedScope = () => $('environment-scope').value;
const current = (scope, token) => scope === selectedScope() && token === generation;
const editable = (scope = renderedScope, token = generation) => !saving && scope !== null && scope === renderedScope && current(scope, token);
function controls() {
  const disabled = !editable();
  $('environment-scope').disabled = saving;
  $('environment-form').querySelectorAll('button, input, textarea').forEach(node => { node.disabled = disabled; });
  $('environment-value').disabled = disabled || $('environment-multiline').checked;
  $('environment-multiline-value').disabled = disabled || !$('environment-multiline').checked;
  $('environment-entries').querySelectorAll('[data-environment-action]').forEach(node => { node.disabled = disabled; });
}
const valueInput = () => $('environment-multiline').checked ? $('environment-multiline-value') : $('environment-value');
function multiline() {
  const active = $('environment-multiline').checked;
  const from = active ? $('environment-value') : $('environment-multiline-value');
  const to = active ? $('environment-multiline-value') : $('environment-value');
  if (!active && from.value.includes('\n')) {
    $('environment-multiline').checked = true;
    $('environment-message').textContent = 'Keep multiline enabled or clear the value before switching to a single line.';
    return;
  }
  to.value = from.value; from.value = '';
  from.hidden = from.disabled = true; from.required = false;
  to.hidden = false; to.required = false;
  controls();
}
$('environment-multiline').onchange = multiline;
const query = scope => scope ? `?project_id=${encodeURIComponent(scope)}` : '';
async function api(path, options) {
  const response = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...options});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Environment request failed');
  return result;
}
function text(tag, value) { const node = document.createElement(tag); node.textContent = value; return node; }
function render(data, scope, token) {
  renderedScope = scope;
  const entries = $('environment-entries'); entries.replaceChildren();
  if (!data.entries.length) entries.append(text('p', 'No variables configured in this scope.'));
  for (const item of data.entries) {
    const row = document.createElement('p');
    row.append(text('strong', item.name), text('span', ` · ${item.state}${item.overrides_host ? ' · overrides host value' : ''}${item.overrides_global ? ' · overrides global value' : ''} `));
    for (const [label, action] of [
      ['Replace', () => { if (!editable(scope, token)) return; $('environment-name').value = item.name; valueInput().focus(); }],
      ...(item.state === 'suppressed' ? [] : [[item.state === 'enabled' ? 'Disable' : 'Enable', () => change(item.name, {state: item.state === 'enabled' ? 'disabled' : 'enabled'}, scope, token)]]),
      ['Delete', () => {
        if (!editable(scope, token)) return;
        const label = scope ? `project ${$('environment-scope').selectedOptions[0].textContent}` : 'global defaults';
        if (confirm(`Delete ${item.name} from ${label}? Stored values cannot be recovered. Deleting an override restores any inherited value.`)) {
          change(item.name, null, scope, token);
        }
      }]
    ]) { const button = text('button', label); button.type = 'button'; button.dataset.environmentAction = ''; button.onclick = action; row.append(button); }
    entries.append(row);
  }
  $('environment-effective').replaceChildren(...(data.effective.length ? data.effective.map(item => text('p', `${item.name} · ${item.source}`)) : [text('p', 'No managed values are active.')]));
  $('environment-host').textContent = data.host_names.join(', ') || 'None';
  $('environment-sessions').replaceChildren(...data.sessions.map(item => text('p', `${item.name} · ${item.status} · ${item.refresh_required ? 'Restart to refresh environment' : 'Current environment'} (revision ${item.revision ?? 'legacy'})`)));
}
async function refresh() {
  const token = ++generation, scope = selectedScope();
  renderedScope = null;
  $('environment-suppress').hidden = !scope;
  $('environment-entries').replaceChildren(text('p', 'Loading variables…'));
  for (const id of ['environment-effective', 'environment-host', 'environment-sessions']) $(id).replaceChildren();
  controls();
  try {
    const data = await api('/api/environment' + query(scope));
    if (!current(scope, token)) return;
    render(data, scope, token);
  } catch (error) {
    if (!current(scope, token)) return;
    $('environment-message').textContent = error.message;
    const retry = text('button', 'Retry loading variables'); retry.type = 'button'; retry.onclick = refresh;
    $('environment-entries').replaceChildren(retry);
  } finally { if (current(scope, token)) controls(); }
}
async function change(name, payload, scope = renderedScope, token = generation, clearDraft = false) {
  if (!editable(scope, token)) return;
  saving = true; controls();
  try {
    await api('/api/environment/' + encodeURIComponent(name) + query(scope), {method: payload === null ? 'DELETE' : 'PUT', ...(payload === null ? {} : {body: JSON.stringify(payload)})});
    if (!current(scope, token)) return;
    if (clearDraft) {
      $('environment-value').value = '';
      $('environment-multiline-value').value = '';
    }
    $('environment-message').textContent = 'Environment saved. New sessions and explicit restarts use the latest values.';
    await refresh();
  } catch (error) { if (current(scope, token)) $('environment-message').textContent = error.message; }
  finally { saving = false; controls(); }
}
$('environment-form').onsubmit = async event => {
  event.preventDefault();
  if (!editable()) return;
  const value = valueInput().value;
  await change($('environment-name').value, {value, state: 'enabled'}, renderedScope, generation, true);
};
$('environment-suppress').onclick = () => {
  if (!editable() || !$('environment-name').reportValidity()) return;
  change($('environment-name').value, {state: 'suppressed'}, renderedScope, generation, true);
};
$('environment-scope').onchange = () => { $('environment-form').reset(); multiline(); $('environment-message').textContent = ''; refresh(); };
controls();
try {
  for (const project of await api('/api/projects')) {
    const option = text('option', project.name); option.value = project.id; $('environment-scope').append(option);
  }
  const project = new URLSearchParams(location.search).get('project_id');
  if (project && [...$('environment-scope').options].some(item => item.value === project)) $('environment-scope').value = project;
} catch (error) { $('environment-message').textContent = error.message; }
await refresh();
