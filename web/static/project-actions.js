// Shared owner project controls for the full desktop and phone panels.
export function projectActions(project, {api, refresh}) {
  const edit = document.createElement('button');
  edit.type = 'button'; edit.textContent = 'Edit project';
  edit.onclick = () => openEditor(project, api, refresh, edit);
  return edit;
}

function openEditor(project, api, refresh, trigger) {
  const dialog = document.createElement('dialog');
  dialog.className = 'project-editor';
  dialog.setAttribute('aria-labelledby', 'project-editor-title');
  dialog.innerHTML = `<form><h2 id="project-editor-title">Edit project</h2>
    <label>Name<input name="name" required maxlength="200"></label>
    <label>Repository<input name="repository"></label>
    <p class="muted">Unassign sessions before changing the repository or deleting this project.</p>
    <label>Description<textarea name="description" rows="3"></textarea></label>
    <label>Status<select name="status"><option value="active">Active</option><option value="paused">Paused</option><option value="completed">Completed</option></select></label>
    <p class="project-editor-status" role="status" aria-live="polite"></p>
    <div class="dialog-actions"><button type="button" data-cancel>Cancel</button><button type="submit">Save project</button><button type="button" class="danger" data-delete>Delete project</button></div>
    <div data-confirm hidden><p>Delete this project and its saved environment variables? This cannot be undone.</p><div class="dialog-actions"><button type="button" data-keep>Keep project</button><button type="button" class="danger" data-confirm-delete>Delete permanently</button></div></div>
    </form>`;
  const form = dialog.querySelector('form');
  const status = dialog.querySelector('.project-editor-status');
  const confirm = dialog.querySelector('[data-confirm]');
  for (const key of ['name','repository','description','status']) form.elements[key].value = project[key] || (key === 'status' ? 'active' : '');
  let pending = false;
  const setPending = value => { pending = value; form.querySelectorAll('input,textarea,select,button').forEach(el => { el.disabled = value; }); };
  const close = () => { if (!pending) dialog.close(); };
  dialog.querySelector('[data-cancel]').onclick = close;
  dialog.addEventListener('cancel', event => { if (pending) event.preventDefault(); });
  dialog.addEventListener('close', () => { dialog.remove(); if (trigger.isConnected) trigger.focus(); });
  dialog.querySelector('[data-delete]').onclick = () => { confirm.hidden = false; dialog.querySelector('[data-keep]').focus(); };
  dialog.querySelector('[data-keep]').onclick = () => { confirm.hidden = true; dialog.querySelector('[data-delete]').focus(); };
  async function mutate(method, body) {
    if (pending) return;
    setPending(true); status.textContent = method === 'DELETE' ? 'Deleting…' : 'Saving…';
    try {
      await api(`/api/projects/${encodeURIComponent(project.id)}`, {method, ...(body ? {body:JSON.stringify(body)} : {})});
    } catch (error) { status.textContent = error.message || 'Project update failed.'; setPending(false); return; }
    setPending(false); dialog.close();
    try { await refresh(); } catch { /* The project list owns its refresh error display. */ }
  }
  form.onsubmit = event => { event.preventDefault(); const data = Object.fromEntries(new FormData(form)); data.name = data.name.trim(); if (!data.name) { status.textContent = 'Enter a project name.'; return; } mutate('PUT', data); };
  dialog.querySelector('[data-confirm-delete]').onclick = () => mutate('DELETE');
  document.body.append(dialog); dialog.showModal(); form.elements.name.focus();
}
