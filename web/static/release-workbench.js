export function setupReleases({api,el,message}) {
  const key=()=>Array.from(crypto.getRandomValues(new Uint8Array(16)),v=>v.toString(16).padStart(2,'0')).join('');
  function action(label,fn){const button=el('button',label);button.type='button';button.onclick=async()=>{if(button.disabled)return;button.disabled=true;try{await fn();}catch(error){message(error.message);}finally{button.disabled=false;}};return button;}
  function field(label){const wrapper=el('label',label),input=el('select');wrapper.append(input);return {wrapper,input};}
  function option(value,label){const node=el('option',label);node.value=value;return node;}
  async function load(root,session,results){
    const loadVersion=Number(root.dataset.releaseRequest||0)+1;root.dataset.releaseRequest=String(loadVersion);
    root.replaceChildren(el('p','Loading release actions…'));
    try{
      const [targets,grants]=await Promise.all([api('/api/workflow/release-targets'),api(`/api/sessions/${session.id}/releases`)]);
      if(!root.isConnected||Number(root.dataset.releaseRequest)!==loadVersion)return;
      root.replaceChildren(el('p','Release only the selected commit to the action and target you authorize. Check results stay tied to that candidate. A small task may use its own recorded checks.','small muted'));
      const historyRoot=el('div');let statusVersion=0,pollTimer=null;
      async function refreshHistory(){
        const ticket=++statusVersion;clearTimeout(pollTimer);
        try{const current=await api(`/api/sessions/${session.id}/releases`);
          if(ticket!==statusVersion||!root.isConnected||Number(root.dataset.releaseRequest)!==loadVersion)return;
          renderHistory(current);
        }catch(error){message(error.message);}
      }
      const candidates=results.filter(r=>r.kind==='final'&&r.outcome==='pass'&&r.artifacts.filter(a=>a.kind==='commit').length===1);
      if(!targets.length)root.append(el('p','No release targets configured. An operator must install an action adapter and a read-only outcome check before releases can run.'));
      else if(!candidates.length)root.append(el('p','Publish a passing final result with the exact commit and actual checks to prepare a release.'));
      else{
        const form=el('form'),candidate=field('Candidate'),target=field('Release target'),operation=field('Action'),evidence=el('fieldset',null,'release-evidence'),legend=el('legend','Check evidence'),preview=el('div');
        candidate.input.append(...candidates.map(r=>option(r.id,`v${r.version} · ${r.artifacts.find(a=>a.kind==='commit').sha.slice(0,12)} · ${r.summary.slice(0,70)}`)));
        target.input.append(...targets.map(t=>option(t.id,t.label)));
        form.append(candidate.wrapper,target.wrapper,operation.wrapper,evidence);root.append(form,preview);
        let generation=0,review=null,submission=null;
        function invalidate(){generation++;review=null;submission=null;preview.replaceChildren();}
        function actions(){operation.input.replaceChildren(...targets.find(t=>t.id===target.input.value).actions.map(a=>option(a,a)));invalidate();}
        async function checks(){invalidate();const ticket=generation;evidence.replaceChildren(legend,el('p','Loading checks…'));
          try{
            const choices=await api('/api/workflow/releases/evidence/'+candidate.input.value);if(ticket!==generation||!root.isConnected)return;
            evidence.replaceChildren(legend);
            if(!choices.length)evidence.append(el('p','No matching check results are available.'));
            for(const choice of choices){const label=el('label'),input=el('input');input.type='checkbox';input.value=choice.id;input.disabled=!choice.eligible;input.checked=choice.eligible&&choice.id===candidate.input.value;label.append(input,el('span',`v${choice.version} · ${choice.summary}`));evidence.append(label);
              for(const check of choice.checks)evidence.append(el('p',check,'small'));
              if(choice.reason)evidence.append(el('p',choice.reason,'small muted'));
            }
          }catch(error){if(ticket===generation)evidence.replaceChildren(legend,el('p',error.message,'danger'));}
        }
        form.addEventListener('input',invalidate);candidate.input.onchange=checks;target.input.onchange=actions;
        const inspect=el('button','Preview release');inspect.type='submit';form.append(inspect);actions();await checks();
        form.onsubmit=async event=>{event.preventDefault();if(inspect.disabled)return;inspect.disabled=true;invalidate();const ticket=++generation;
          const request={candidate_result_id:candidate.input.value,evidence_result_ids:[...evidence.querySelectorAll('input:checked')].map(i=>i.value),action:operation.input.value,target:target.input.value};
          try{
            const view=await api('/api/workflow/releases/preview',request);if(ticket!==generation||!root.isConnected)return;review=view;
            submission={...request,expected_hash:view.hash,request_key:key()};const runKey=key();
            preview.replaceChildren(el('h3','Review release'),el('p',`${view.action} → ${view.target_label}`),el('p',view.candidate_sha,'overview-meta'),el('p',`${view.evidence.length} matching check result(s). This authorizes only this candidate, action and target.`,'small'));
            const status=el('p');status.setAttribute('role','status');
            const run=action('Authorize and run',async()=>{
              if(!review||!submission)return;
              try{
                const grant=await api('/api/workflow/releases/authorize',submission);
                await api(`/api/workflow/releases/${grant.id}/attempts`,{mode:'apply',request_key:runKey});
                review=null;submission=null;preview.replaceChildren();await refreshHistory();
              }catch(error){status.textContent=error.message+' Check this request or refresh release status before starting another.';run.textContent='Check release request';}
            });preview.append(run,status);
          }catch(error){if(ticket===generation)preview.replaceChildren(el('p',error.message,'danger'));}
          finally{inspect.disabled=false;}
        };
      }
      if(!root.isConnected||Number(root.dataset.releaseRequest)!==loadVersion)return;
      root.append(action('Refresh release status',refreshHistory),historyRoot);renderHistory(grants);
      function renderHistory(current){
      historyRoot.replaceChildren();
      for(const grant of current){
        const attempt=grant.attempts.at(-1),view=grant.preview,card=el('article',null,'panel');
        card.append(el('h3',`${view.action} → ${view.target_label}`),el('p',view.candidate_sha,'overview-meta'),el('p',attempt?`${attempt.state}${attempt.active?' · adapter active':''}`:'Authorized · not started','badge'));
        if(attempt?.summary)card.append(el('p',attempt.summary));if(attempt?.external_reference)card.append(el('p',attempt.external_reference,'overview-meta'));
        const send=mode=>{const requestKey=key();return async()=>{await api(`/api/workflow/releases/${grant.id}/attempts`,{mode,request_key:requestKey});await refreshHistory();};};
        if(!attempt)card.append(action('Run authorized release',send('apply')));
        else if(!attempt.active){
          if(attempt.state==='not-applied')card.append(action('Retry authorized release',send('apply')));
          if(attempt.state!=='applied')card.append(action('Check external outcome',send('probe')));
        }
        const history=el('details');history.append(el('summary','Release history'));
        for(const old of grant.attempts)history.append(el('p',`${old.created_at} · ${old.mode} · ${old.state} · ${old.summary}`,'small'));
        card.append(history);historyRoot.append(card);
      }
      clearTimeout(pollTimer);
      if(current.some(g=>{const a=g.attempts.at(-1);return a&&(a.active||['queued','running'].includes(a.state));}))pollTimer=setTimeout(()=>{if(root.isConnected&&Number(root.dataset.releaseRequest)===loadVersion)refreshHistory();},1500);
      }
    }catch(error){root.replaceChildren(el('p',error.message,'danger'),action('Retry release status',()=>load(root,session,results)));}
  }
  return {load};
}
