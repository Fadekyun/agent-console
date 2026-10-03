import { setupWorkflow } from '/static/workflow-workbench.js';
import { setupConnections } from '/static/connections-workbench.js';
import { setupReleases } from '/static/release-workbench.js';
function requestKey(){return Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');}
export function setupResults({api,el,message,sessions,editStep,openSession}) {
  const workflow=setupWorkflow({api,el,message,editStep,openSession});
  const connections=setupConnections({api,el,message,sessions});
  const releases=setupReleases({api,el,message});
  const root=document.querySelector('#session-results');let selected=null,generation=0;
  function action(label,fn){const button=el('button',label);button.type='button';button.onclick=async()=>{button.disabled=true;try{await fn();}catch(error){message(error.message);}finally{button.disabled=false;}};return button;}
  function field(label,tag='input'){const wrapper=el('label',label),input=el(tag);wrapper.append(input);return {wrapper,input};}
  function option(value,label=value){const node=el('option',label);node.value=value;return node;}
  function renderResult(result, targets){
    const card=el('article',null,'panel');
    card.append(el('h3',`${result.kind === 'final' ? 'Final result' : 'Ready checkpoint'} · v${result.version} · ${result.outcome}`),el('p',result.summary),el('p',`${result.created_at} · ${result.session_id}`,'small muted'));
    for(const check of result.checks)card.append(el('p',check,'small'));
    for(const [index,artifact] of result.artifacts.entries()){
      const link=el('a',`${artifact.label} · ${artifact.sha?.slice(0,12)||artifact.hash.slice(0,12)}`);link.href=`/api/results/${result.id}/artifacts/${index}`;link.download='';card.append(link,el('br'));
    }
    if(targets.length){
      const target=field('Hand off this version to','select');target.input.append(...targets.map(s=>option(s.id,s.tmux_name)));
      const note=field('Handoff note','textarea');note.input.rows=2;
      const handoff=el('details');handoff.append(el('summary','Send this version'));
      let key=requestKey();handoff.append(target.wrapper,note.wrapper,action('Queue handoff',async()=>{
        await api(`/api/results/${result.id}/send`,{target_session_id:target.input.value,note:note.input.value,request_key:key});key=requestKey();message('Handoff queued. Delivery and consumption are acknowledged separately.');
      }));
      card.append(handoff);
    }
    return card;
  }
  async function load(session,draft=null){
    const token=++generation;
    const isCurrent=()=>selected===session.id&&token===generation;
    selected=session.id;root.hidden=false;root.replaceChildren(el('p','Loading results and inputs…'));
    let results,inbox;
    try { [results,inbox]=await Promise.all([api(`/api/sessions/${session.id}/results`),api(`/api/sessions/${session.id}/inbox`)]); }
    catch(error){
      if(selected===session.id&&token===generation)root.replaceChildren(el('p',error.message),action('Retry loading results',()=>load(session,draft)));
      return;
    }
    if(selected!==session.id||token!==generation)return;
    root.replaceChildren();
    const form=el('form'),kind=field('Result type','select'),outcome=field('Outcome','select'),summary=field('Summary','textarea'),checks=field('Checks performed (one per line)','textarea'),files=field('Selected files (one repository-relative path per line)','textarea'),commit=field('Exact commit SHA (optional)');
    kind.input.append(option('final','Final result'),option('ready','Ready checkpoint'));outcome.input.append(option('pass','Passed'),option('fail','Failed'),option('blocked','Blocked'));
    summary.input.required=true;summary.input.maxLength=32000;summary.input.rows=3;
    form.append(el('p','A small task can finish here. Publish only the checks and artifacts this session actually produced.','small muted'),kind.wrapper,outcome.wrapper,summary.wrapper);
    const details=el('details');details.append(el('summary','Checks & selected artifacts'),checks.wrapper,files.wrapper,commit.wrapper);form.append(details);
    const submit=el('button','Publish result','primary');submit.type='submit';
    const status=el('p');status.setAttribute('role','alert');form.append(status,submit);
    const draftFields={kind:kind.input,outcome:outcome.input,summary:summary.input,checks:checks.input,files:files.input,commit:commit.input};
    if(draft)for(const [key,input] of Object.entries(draftFields))input.value=draft.values[key];
    details.open=!!draft?.artifactsOpen;status.textContent=draft?.error||'';
    let publishing=false,refreshDeferred=false;
    function reloadCurrent(preserveDraft=false){
      if(!isCurrent())return;
      if(publishing){refreshDeferred=true;return;}
      const saved=preserveDraft?{values:Object.fromEntries(Object.entries(draftFields).map(([key,input])=>[key,input.value])),open:publish.open,artifactsOpen:details.open,error:status.textContent,requestKey:key}:null;
      return load(session,saved);
    }
    let key=draft?.requestKey||requestKey();form.onsubmit=async event=>{event.preventDefault();if(submit.disabled)return;publishing=true;const controls=[...form.elements].map(control=>[control,control.disabled]);for(const [control] of controls)control.disabled=true;status.textContent='';try{
      const lines=input=>input.value.split('\n').map(s=>s.trim()).filter(Boolean);
      const artifacts=lines(files.input).map(path=>({kind:'file',path}));if(commit.input.value.trim())artifacts.push({kind:'commit',sha:commit.input.value.trim()});
      await api(`/api/sessions/${session.id}/results`,{kind:kind.input.value,outcome:outcome.input.value,summary:summary.input.value,checks:lines(checks.input),artifacts,request_key:key});key=requestKey();publishing=false;refreshDeferred=false;message(`${session.tmux_name}: result published. Its selected artifact snapshots are preserved.`);await reloadCurrent();
    }catch(error){if(isCurrent())status.textContent=error.message;else message(`${session.tmux_name}: ${error.message}`);}finally{publishing=false;for(const [control,disabled] of controls)control.disabled=disabled;if(refreshDeferred){refreshDeferred=false;await reloadCurrent(true);}}};
    const publish = el('details',null,'panel');publish.append(el('summary','Publish a result'),form);publish.open=!!draft?.open;
    const connected=el('details',null,'panel'),connectionRoot=el('div');connected.append(el('summary','Connected inputs'),connectionRoot);
    let loaded=false;connected.ontoggle=()=>{if(connected.open&&!loaded){loaded=true;connections.load(connectionRoot,session,()=>reloadCurrent(true));}};
    const next=el('details',null,'panel'),nextRoot=el('div');next.id='workflow-next-steps';next.append(el('summary','Next steps'),nextRoot);
    let nextLoaded=false;next.ontoggle=()=>{if(next.open&&!nextLoaded){nextLoaded=true;workflow.load(nextRoot,session);}};
    const release=el('details',null,'panel'),releaseRoot=el('div');release.id='workflow-releases';release.append(el('summary','Release actions'),releaseRoot);
    let releaseLoaded=false;release.ontoggle=()=>{if(release.open&&!releaseLoaded){releaseLoaded=true;releases.load(releaseRoot,session,results.results);}};
    root.append(next,connected,release);
    root.append(publish,el('h2','Inbox'),el('p',inbox.notice,'small muted'));
    if(!inbox.items.length)root.append(el('p','No handoffs yet.'));
    function inputCard(item){
      const card=renderResult(item.result,[]);card.prepend(el('p',`Input ${item.sequence} · ${item.state} · from ${item.source_session_id}`,'badge'));
      if(item.note)card.append(el('p',item.note));
      if(item.state!=='consumed')card.append(action(item.state==='queued'?'Acknowledge delivery':'Mark consumed',async()=>{
        await api(`/api/sessions/${session.id}/inbox/${item.id}/ack`,{state:item.state==='queued'?'delivered':'consumed'});await reloadCurrent(true);
      }));return card;
    }
    const inputList=el('div');root.append(inputList);
    for(const item of inbox.items)inputList.append(inputCard(item));
    if(inbox.items.length===5){
      let after=inbox.next_sequence;
      const more=action('More inputs',async()=>{
        const page=await api(`/api/sessions/${session.id}/inbox?after=${after}`);
        for(const item of page.items)inputList.append(inputCard(item));
        after=page.next_sequence;if(page.items.length<5)more.remove();
      });root.append(more);
    }
    root.append(el('h2','Published versions'));
    const targets=sessions().filter(s=>s.id!==session.id&&s.managed&&s.execution_kind!=='integration-plan');
    if(!results.results.length)root.append(el('p','No explicit ready or final result has been published. Terminal output and attention status do not publish a result automatically.'));
    for(const result of results.results)root.append(renderResult(result,targets));
    if(results.results.length===5){
      let before=results.results.at(-1).version;
      const more=action('Older results',async()=>{
        const page=await api(`/api/sessions/${session.id}/results?before=${before}`);
        if(selected!==session.id||token!==generation)return;
        const known=new Set(results.results.map(result=>result.id));
        for(const result of page.results)if(!known.has(result.id)){
          known.add(result.id);results.results.push(result);more.before(renderResult(result,targets));
        }
        if(releaseLoaded){
          releaseLoaded=false;
          if(release.open){releaseLoaded=true;await releases.load(releaseRoot,session,results.results);}
        }
        if(page.results.length)before=page.results.at(-1).version;
        if(page.results.length<5)more.remove();
      });root.append(more);
    }
  }
  return {load};
}
