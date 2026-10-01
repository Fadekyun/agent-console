export function setupLaunches({api,el,state,openCreate,getRequest,refresh,openSession,message}) {
  const $=selector=>document.querySelector(selector),form=$('#create-form');
  let sequence=0,review=null,launching=false;
  const labels={tool:'Harness',profile:'Role',repository:'Repository',worktree:'Isolated worktree',auth_context:'Account',agent_mode:'Mode',provider:'Provider',model:'Model',reasoning_effort:'Reasoning effort',plan_reasoning_effort:'Plan reasoning effort',project_id:'Project'};
  function explain(target,value){
    const list=el('dl',null,'configuration-list');
    for(const [key,label] of Object.entries(labels)){
      list.append(el('dt',label),el('dd',value.config[key]??(['model','reasoning_effort','plan_reasoning_effort'].includes(key)?'Harness default · not pinned':'Not set')));
    }
    target.append(list);
    if(value.workspace)target.append(el('p','Workspace: '+value.workspace,'overview-meta'));
    if(value.permission_mode)target.append(el('p','Permissions: '+value.permission_mode));
    for(const warning of value.warnings||[])target.append(el('p',warning,'small muted'));
    const details=el('details'),summary=el('summary','Role, launcher & skill revisions');details.append(summary);
    if(value.profile_hash)details.append(el('p','Role hash: '+value.profile_hash,'overview-meta'));
    if(value.launcher)details.append(el('p',`Launcher: ${value.launcher.path} · version ${value.launcher.version||'unknown'} · ${value.launcher.sha256}`,'overview-meta'));
    for(const skill of value.skills||[])details.append(el('p',`${skill.name} · ${skill.hash}`,'overview-meta'));
    if(!value.skills?.length)details.append(el('p','No Console-selected skills.','small'));
    target.append(details);
  }
  function invalidate(){sequence++;review=null;$('#launch-preview').hidden=true;}
  function reset(parent){
    invalidate();form.dataset.launchConfig='';form.dataset.recipe='';form.dataset.continuation='';form.dataset.review='';
    $('#recipe-save-panel').hidden=!!parent;$('#preview-launch').hidden=!!parent;
    $('#save-recipe').textContent='Save recipe';
  }
  function selectValue(control,value){
    if(control.tagName==='SELECT'&&![...control.options].some(o=>o.value===value)){
      const option=el('option',value+' · check availability');option.value=value;control.append(option);
    }
    control.value=value;
  }
  function populate(request,{recipe=null,source=null}={}){
    openCreate();
    for(const key of ['tool','profile'])if(request[key]){selectValue(form.elements[key],request[key]);form.elements[key].dispatchEvent(new Event('change'));}
    for(const [key,value] of Object.entries(request)){
      const control=form.elements[key];if(!control||value==null||key==='name')continue;
      if(control.type==='checkbox')control.checked=value;else selectValue(control,String(value));
    }
    form.dataset.launchConfig=JSON.stringify(request);form.dataset.review='true';
    if(recipe){form.dataset.recipe=JSON.stringify(recipe);form.elements.recipe_title.value=recipe.title;$('#save-recipe').textContent='Update recipe';}
    if(source){form.dataset.continuation=source.id;$('#create-title').textContent='Continue work';$('#create-help').textContent='Start a new conversation using recorded settings and the preserved workspace. The latest explicit result will be delivered as an input.';$('#recipe-save-panel').hidden=true;}
    form.querySelector('button[type=submit]').textContent=source?'Review continuation':'Review launch';
    $('#create-start-help').textContent='Preview settings and skill revisions, then launch. The task stays in the terminal composer until you send it.';
  }
  async function reviewLaunch(request=getRequest()){
    const token=++sequence;review=null;const panel=$('#launch-preview'),button=$('#preview-launch');button.disabled=true;
    panel.hidden=false;panel.replaceChildren(el('p','Checking launch configuration…'));
    try{
      const source=form.dataset.continuation||null;
      const view=await api('/api/workbench/launches/preview',{request,source_session_id:source});
      if(token!==sequence)return;
      const key=Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');
      review={request,source_session_id:source,expected_hash:view.hash,request_key:key};
      panel.replaceChildren(el('h3','Review launch'));explain(panel,view);
      const task=el('p',view.task||'No initial task','brief');panel.append(el('strong','Task'),task);
      const run=el('button','Launch session','primary');run.type='button';run.id='confirm-launch';
      const result=el('p');result.setAttribute('role','status');panel.append(run,result);
      run.onclick=async()=>{
        if(!review||launching)return;launching=true;run.disabled=true;const submission=review;
        try{
          const launched=await api('/api/workbench/launches',submission);
          if(launched.state!=='created'){result.textContent=launched.error||'Launch is still pending. Check again before starting another session.';run.textContent='Check launch';return;}
          $('#create-dialog').close();await refresh();openSession(launched.name);
        }catch(error){result.textContent=error.message+' Check this launch before starting another.';run.textContent='Check launch';}
        finally{launching=false;run.disabled=false;}
      };
    }catch(error){if(token===sequence)panel.replaceChildren(el('p',error.message,'danger'));}
    finally{button.disabled=false;}
  }
  async function recipes(){
    const list=$('#recipe-list');list.replaceChildren(el('p','Loading recipes…'));$('#recipe-dialog').showModal();
    try{
      const entries=await api('/api/workbench/recipes');list.replaceChildren();
      if(!entries.length)list.append(el('p','No recipes yet. Open New session, choose a task and settings, then Save a reusable recipe.'));
      for(const recipe of entries){
        const card=el('article',null,'panel');card.append(el('h3',recipe.title),el('p',recipe.request.task||'No stored task','brief'),el('p',`${recipe.request.profile} · ${recipe.request.tool} · revision ${recipe.revision}`,'small muted'));
        const actions=el('div',null,'actions'),use=el('button','Use recipe'),edit=el('button','Edit'),remove=el('button','Remove','danger');actions.append(use,edit,remove);card.append(actions);list.append(card);
        use.onclick=()=>{$('#recipe-dialog').close();populate(recipe.request);};
        edit.onclick=()=>{$('#recipe-dialog').close();populate(recipe.request,{recipe});$('#recipe-save-panel').open=true;};
        remove.onclick=async()=>{if(!confirm(`Remove recipe “${recipe.title}”? Existing sessions stay unchanged.`))return;remove.disabled=true;try{await api('/api/workbench/recipes/'+recipe.id+'/remove',{expected_revision:recipe.revision});card.remove();}catch(error){message(error.message);}finally{remove.disabled=false;}};
      }
    }catch(error){list.replaceChildren(el('p',error.message,'danger'));}
  }
  $('#run-recipe').onclick=recipes;$('#close-recipes').onclick=()=>$('#recipe-dialog').close();
  $('#preview-launch').onclick=()=>{if(form.reportValidity())reviewLaunch();};
  $('#save-recipe').onclick=async()=>{
    if(!form.reportValidity())return;
    if(!form.elements.recipe_title.value.trim()){$('#create-error').textContent='Name this recipe before saving.';form.elements.recipe_title.focus();return;}
    const button=$('#save-recipe');button.disabled=true;$('#create-error').textContent='';
    try{
      const old=form.dataset.recipe?JSON.parse(form.dataset.recipe):null;
      const saved=await api('/api/workbench/recipes'+(old?'/'+old.id:''),{title:form.elements.recipe_title.value.trim(),request:getRequest(),expected_revision:old?.revision||null});
      form.dataset.recipe=JSON.stringify(saved);button.textContent='Update recipe';message('Recipe saved. It has not started a session.');
    }catch(error){$('#create-error').textContent=error.message;}finally{button.disabled=false;}
  };
  $('#show-configuration').onclick=async()=>{
    const selected=state.selected,button=$('#show-configuration');if(!selected)return;button.disabled=true;
    try{
      const data=await api(`/api/workbench/sessions/${selected.id}/configuration`);if(state.selected?.id!==selected.id)return;
      const panel=$('#session-configuration');panel.replaceChildren(el('h3','Launch configuration'),el('p',data.notice,'small muted'));panel.hidden=false;
      if(data.latest){explain(panel,data.latest);panel.append(el('p','Recorded '+new Date(data.latest.created_at).toLocaleString(),'small'));if(data.latest.invalidated)panel.append(el('p',data.latest.invalidated,'danger'));}
      else explain(panel,{config:data.known});
      if(data.receipts.length>1)panel.append(el('p',`${data.receipts.length} configuration records retained. Latest record shown.`,'small'));
    }catch(error){message(error.message);}finally{button.disabled=false;}
  };
  $('#continue-session').onclick=async()=>{
    const source=state.selected,button=$('#continue-session');if(!source)return;button.disabled=true;
    try{
      const data=await api(`/api/workbench/sessions/${source.id}/configuration`);
      if(!data.latest||data.latest.invalidated){
        const panel=$('#session-configuration');panel.hidden=false;panel.replaceChildren(el('p',data.latest?.invalidated||data.notice));
        const draft=el('button','New session from known settings');panel.append(draft);
        draft.onclick=()=>{populate({...data.known,task:source.initial_task||''});$('#create-help').textContent='Review these known settings for a new session. This does not restore the previous conversation or reuse its worktree.';};
        return;
      }
      populate({...data.latest.config,task:source.initial_task||'Continue from the latest result and preserved workspace.'},{source});
    }catch(error){message(error.message);}finally{button.disabled=false;}
  };
  form.addEventListener('input',invalidate);form.addEventListener('change',invalidate);
  return {reset,reviewLaunch};
}
