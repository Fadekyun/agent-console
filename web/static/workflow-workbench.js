export function setupWorkflow({api,el,message,editStep,openSession}) {
  function action(label,fn){const button=el('button',label);button.type='button';button.onclick=async()=>{button.disabled=true;try{await fn();}catch(error){message(error.message);}finally{button.disabled=false;}};return button;}
  function field(label,tag='input'){const wrapper=el('label',label),input=el(tag);input.setAttribute('aria-label',label);wrapper.append(input);return {wrapper,input};}
  function option(value,label=value){const node=el('option',label);node.value=value;return node;}
  async function load(root,session){
    root.replaceChildren(el('p','Loading next steps…'));
    try{
      const flow=await api(`/api/sessions/${session.id}/workflow`),policy=flow.policy.policy;
      root.replaceChildren(el('p',policy.mode==='auto'?'Auto within reviewed limits':'Suggestions only · nothing starts until accepted','badge'),el('p','A small task can finish in one session. Add another only for a distinct useful job.','small muted'));
      const controls=el('div',null,'actions');controls.append(action('Refresh next steps',()=>load(root,session)));
      if(flow.policy.state!=='stopped'){
        controls.append(action(flow.policy.state==='paused'?'Resume workflow':'Pause workflow',async()=>{await api(`/api/sessions/${flow.root_id}/workflow/control`,{state:flow.policy.state==='paused'?'running':'paused'});await load(root,session);}));
        controls.append(action('Stop workflow',async()=>{if(!confirm('Stop new dispatch and interrupt the sessions connected to this workflow? Results, files and history are preserved.'))return;await api(`/api/sessions/${flow.root_id}/workflow/control`,{state:'stopped'});await load(root,session);}));
      }else root.append(el('p','Workflow stopped. Its history is preserved.','badge'));
      root.append(controls);
      if(!flow.steps.length)root.append(el('p','No extra session proposed. Continue this task here, or use Add session for a specific next step.'));
      for(const step of flow.steps){
        const card=el('article',null,'panel'),attempt=step.attempts.at(-1),status=attempt?.state||step.decision;
        card.append(el('h3',step.task),el('p',status+(attempt?.result?' · '+attempt.result.outcome:''),'badge'),el('p',step.reason),el('p','Expected: '+step.expected_output,'small muted'),el('p',`${step.config.profile} · ${step.config.tool} · ${step.config.action} · ${step.config.repository}`,'small muted'));
        for(const dependency of step.dependencies)card.append(el('p',`${dependency.readiness} · ${dependency.source_id}`,'small muted'));
        if(step.error||attempt?.error)card.append(el('p',step.error||attempt.error,'small'));
        const actions=el('div',null,'actions');
        if(step.decision==='proposed'&&flow.policy.state!=='stopped'){
          const previewBox=el('div');
          actions.append(action('Preview launch',async()=>{
            const preview=await api(`/api/workflow/steps/${step.id}/preview`,{});
            previewBox.replaceChildren(el('p',`${preview.config.profile} using ${preview.config.tool}/${preview.config.auth_context} · ${preview.adapter?.version||'native adapter'} · ${preview.config.worktree?'isolated worktree':'existing repository'}`,'small'));
            for(const skill of preview.skills)previewBox.append(el('p',`${skill.name} · ${skill.hash.slice(0,12)}`,'small muted'));
            if(!preview.skills.length)previewBox.append(el('p','No Console-selected skills.','small muted'));
            previewBox.append(el('p','Accepting authorizes this task and configuration to start when its required inputs and capacity are ready.','small'),action('Accept next step',async()=>{await api(`/api/workflow/steps/${step.id}/review`,{decision:'accepted',expected_version:step.version,preview_hash:preview.hash});await load(root,session);}));
          }),action('Reject suggestion',async()=>{await api(`/api/workflow/steps/${step.id}/review`,{decision:'rejected',expected_version:step.version});await load(root,session);}));
          card.append(actions,previewBox);
        }else card.append(actions);
        if(flow.policy.state!=='stopped')actions.append(action('Edit next step',()=>editStep(step)));
        if(attempt){
          actions.append(action('Open session',()=>openSession(attempt.name)));
          if(['completed','failed','cancelled'].includes(attempt.state)&&flow.policy.state!=='stopped')actions.append(action('Retry when ready',async()=>{await api(`/api/workflow/steps/${step.id}/retry`,{});await load(root,session);}));
          if(attempt.state==='unknown'){
            const resolve=el('details'),form=el('form'),outcome=field('Observed outcome','select'),evidence=field('Reconciliation evidence','textarea');
            outcome.input.append(option('not-started','Confirmed not started'),option('blocked','Blocked'),option('fail','Failed'),option('pass','Passed'));evidence.input.required=true;evidence.input.maxLength=4000;
            resolve.append(el('summary','Resolve uncertain attempt'),el('p','Check the session and any affected systems first. Recording an outcome preserves history and does not retry the task.','small muted'),form);
            const submit=el('button','Record observed outcome');submit.type='submit';form.append(outcome.wrapper,evidence.wrapper,submit);
            form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{await api(`/api/workflow/attempts/${attempt.id}/reconcile`,{outcome:outcome.input.value,summary:evidence.input.value});await load(root,session);}catch(error){message(error.message);}finally{submit.disabled=false;}};
            card.append(resolve);
          }
          const history=el('details');history.append(el('summary',`${step.attempts.length} attempt${step.attempts.length===1?'':'s'}`));
          for(const item of step.attempts)history.append(el('p',`Attempt ${item.generation} · ${item.state} · ${item.name}`,'small'));
          card.append(history);
        }
        root.append(card);
      }
      const settings=el('details'),form=el('form');settings.append(el('summary','Workflow mode & limits'),form);
      const mode=field('Expansion mode','select');mode.input.append(option('suggestions','Suggestions only'),option('auto','Auto within limits'));mode.input.value=policy.mode;form.append(mode.wrapper);
      const scopes={};for(const [key,label] of [['repositories','Repositories (one per line)'],['actions','Actions: read, write, test (one per line)'],['roles','Allowed roles (one per line)'],['harnesses','Allowed harnesses (one per line)'],['targets','Authorized targets (one per line, if any)']]){
        const item=field(label,'textarea');item.input.rows=2;item.input.value=policy[key].join('\n');scopes[key]=item.input;form.append(item.wrapper);
      }
      const limits={};for(const [key,label] of [['max_concurrent','Concurrent sessions'],['max_total','Total session budget'],['max_depth','Descendant depth'],['max_reruns','Recomputations per step']]){
        const item=field(label);item.input.type='number';item.input.min=key==='max_reruns'?0:1;item.input.value=policy[key];limits[key]=item.input;form.append(item.wrapper);
      }
      form.append(el('p','The original and connected live sessions count toward concurrency. Every launched attempt counts toward the total budget. Each native attempt is limited to 30 minutes. New scopes or larger limits require this explicit review.','small muted'));
      const submit=el('button','Save reviewed policy');submit.type='submit';submit.disabled=flow.policy.state==='stopped';form.append(submit);
      form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{
        const next={...policy,mode:mode.input.value};for(const [key,input] of Object.entries(scopes))next[key]=input.value.split('\n').map(v=>v.trim()).filter(Boolean);for(const [key,input] of Object.entries(limits))next[key]=Number(input.value);
        await api(`/api/sessions/${flow.root_id}/workflow/policy`,{expected_version:flow.policy.version,policy:next});message('Workflow policy recorded. Existing running attempts retain their accepted scope.');await load(root,session);
      }catch(error){message(error.message);}finally{submit.disabled=false;}};
      root.append(settings);
    }catch(error){root.replaceChildren(el('p',error.message),action('Retry loading next steps',()=>load(root,session)));}
  }
  return {load};
}
