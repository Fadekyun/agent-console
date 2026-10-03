const $ = id => document.getElementById(id);
let generation = 0;
let saving = false;
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
  to.hidden = to.disabled = false; to.required = true;
}
$('environment-multiline').onchange = multiline;
const query = () => $('environment-scope').value ? `?project_id=${encodeURIComponent($('environment-scope').value)}` : '';
async function api(path, options) {
  const response = await fetch(path, {headers: {'Content-Type': 'application/json'}, ...options});
  const result = await response.json();
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : 'Environment request failed');
  return result;
}
function text(tag, value) { const node = document.createElement(tag); node.textContent = value; return node; }
function render(data) {
  const entries = $('environment-entries'); entries.replaceChildren();
  if (!data.entries.length) entries.append(text('p', 'No variables configured in this scope.'));
  for (const item of data.entries) {
    const row = document.createElement('p');
    row.append(text('strong', item.name), text('span', ` · ${item.state}${item.overrides_host ? ' · overrides host value' : ''}${item.overrides_global ? ' · overrides global value' : ''} `));
    for (const [label, action] of [
      ['Replace', () => { $('environment-name').value = item.name; valueInput().focus(); }],
      ...(item.state === 'suppressed' ? [] : [[item.state === 'enabled' ? 'Disable' : 'Enable', () => change(item.name, {state: item.state === 'enabled' ? 'disabled' : 'enabled'})]]),
      ['Delete', () => change(item.name, null)]
    ]) { const button = text('button', label); button.type = 'button'; button.onclick = action; row.append(button); }
    entries.append(row);
  }
  $('environment-effective').replaceChildren(...(data.effective.length ? data.effective.map(item => text('p', `${item.name} · ${item.source}`)) : [text('p', 'No managed values are active.')]));
  $('environment-host').textContent = data.host_names.join(', ') || 'None';
  $('environment-sessions').replaceChildren(...data.sessions.map(item => text('p', `${item.name} · ${item.status} · ${item.refresh_required ? 'Restart to refresh environment' : 'Current environment'} (revision ${item.revision ?? 'legacy'})`)));
}
async function refresh() {
  const token = ++generation;
  $('environment-suppress').hidden = !$('environment-scope').value;
  try { const data = await api('/api/environment' + query()); if (token === generation) render(data); }
  catch (error) { if (token === generation) $('environment-message').textContent = error.message; }
}
async function change(name, payload) {
  if (saving) return;
  saving = true;
  const selected = query();
  const controls = [...document.querySelectorAll('button, select')]; controls.forEach(node => node.disabled = true);
  try {
    await api('/api/environment/' + encodeURIComponent(name) + selected, {method: payload === null ? 'DELETE' : 'PUT', ...(payload === null ? {} : {body: JSON.stringify(payload)})});
    $('environment-message').textContent = 'Environment saved. New sessions and explicit restarts use the latest values.';
    await refresh();
  } catch (error) { $('environment-message').textContent = error.message; }
  finally { saving = false; controls.forEach(node => node.disabled = false); }
}
$('environment-form').onsubmit = async event => {
  event.preventDefault();
  const value = valueInput().value;
  valueInput().value = '';
  await change($('environment-name').value, {value, state: 'enabled'});
};
$('environment-suppress').onclick = () => {
  if (!$('environment-name').reportValidity()) return;
  valueInput().value = '';
  change($('environment-name').value, {state: 'suppressed'});
};
$('environment-scope').onchange = () => { $('environment-form').reset(); multiline(); refresh(); };
try {
  for (const project of await api('/api/projects')) {
    const option = text('option', project.name); option.value = project.id; $('environment-scope').append(option);
  }
  const project = new URLSearchParams(location.search).get('project_id');
  if (project && [...$('environment-scope').options].some(item => item.value === project)) $('environment-scope').value = project;
} catch (error) { $('environment-message').textContent = error.message; }
await refresh();
