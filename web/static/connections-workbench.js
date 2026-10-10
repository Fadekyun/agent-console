export function setupConnections({api,el,message,sessions}) {
  function button(text,fn){const node=el('button',text);node.type='button';node.onclick=async()=>{node.disabled=true;try{await fn();}catch(error){message(error.message);}finally{node.disabled=false;}};return node;}
  function readinessSelect(){const select=el('select');for(const [value,text] of [['after-final','After final result'],['after-ready','After ready checkpoint'],['alongside','Alongside · current snapshot']]){const option=el('option',text);option.value=value;select.append(option);}return select;}
  async function load(root,session,onDelivered){
    root.replaceChildren(el('p','Loading connections…'));
    try{
      const graph=await api(`/api/sessions/${session.id}/connections`),all=sessions(),names=new Map(all.map(s=>[s.id,s.tmux_name]));
      root.replaceChildren(el('p','Connect existing sessions without restarting them. Ownership and required inputs are separate. Queue inputs when ready; each recipient acknowledges and uses them in its own session.','small muted'));
      const members=graph.nodes.length?graph.nodes:[{session_id:session.id,owner_id:null}];
      for(const node of members)if(names.has(node.native_session_id))names.set(node.session_id,names.get(node.native_session_id));
      for(const node of members){
        const card=el('article',null,'panel'),state=graph.readiness[node.session_id];
        const nativeKnown=names.has(node.session_id);const link=el(nativeKnown?'a':'span',names.get(node.session_id)||'Planned session');if(nativeKnown)link.href=`#session/${encodeURIComponent(names.get(node.session_id))}`;
        card.append(link,el('p',node.owner_id?`Owned by ${names.get(node.owner_id)||node.owner_id}`:'Initial session','small muted'));
        if(node.purpose)card.append(el('p',node.purpose));
        if(state&&nativeKnown){
          card.append(el('p',state.blocked?'Waiting for required inputs':state.stale?'Inputs changed · result needs recomputation':state.delivered?'Inputs queued':'Ready to receive inputs','badge'));
          for(const reason of state.reasons)card.append(el('p',reason,'small muted'));
          if(state.delivered||graph.edges.some(e=>e.target_id===node.session_id)){
            const queue=button('Queue ready inputs',async()=>{await api(`/api/sessions/${node.session_id}/connections/deliver`,{expected_version:graph.version,expected_signature:state.signature});message('Connected inputs queued. Acknowledge delivery and consumption before publishing the resulting work.');await onDelivered();});queue.disabled=state.blocked;card.append(queue);
          }
          const edit=el('details');edit.append(el('summary','Required inputs'));const inputs=[];
          for(const source of members.filter(n=>n.session_id!==node.session_id)){
            const label=el('label',null,'check'),check=el('input');check.type='checkbox';const edge=graph.edges.find(e=>e.target_id===node.session_id&&e.source_id===source.session_id);
            check.checked=!!edge;label.append(check,document.createTextNode(names.get(source.session_id)||source.session_id));
            const select=readinessSelect();select.setAttribute('aria-label',`Readiness for ${names.get(source.session_id)||source.session_id}`);if(edge)select.value=edge.readiness;
            edit.append(label,select);inputs.push({check,select,source});
          }
          edit.append(button('Save required inputs',async()=>{await api(`/api/sessions/${node.session_id}/connections/dependencies`,{expected_version:graph.version,dependencies:inputs.filter(i=>i.check.checked).map(i=>({source_id:i.source.session_id,readiness:i.select.value}))});await load(root,session,onDelivered);}));card.append(edit);
        }
        root.append(card);
      }
      const candidates=all.filter(s=>s.managed&&s.execution_kind!=='integration-plan'&&!members.some(n=>n.session_id===s.id||n.native_session_id===s.id));
      if(candidates.length){
        const form=el('form'),targetLabel=el('label','Existing session'),target=el('select');
        for(const s of candidates){const option=el('option',`${s.tmux_name} · ${s.profile}`);option.value=s.id;target.append(option);}targetLabel.append(target);
        const purposeLabel=el('label','Purpose'),purpose=el('textarea');purpose.rows=2;purpose.required=true;purpose.maxLength=4000;purposeLabel.append(purpose);
        const readinessLabel=el('label','Use this session’s result'),readiness=readinessSelect();readiness.setAttribute('aria-label','Use this session’s result');readinessLabel.append(readiness);
        const receipt=el('div');target.onchange=()=>receipt.replaceChildren();form.append(targetLabel,purposeLabel,readinessLabel,button('Inspect existing skill snapshot',async()=>{
          const selected=all.find(s=>s.id===target.value);const data=await api(`/api/sessions/${encodeURIComponent(selected.tmux_name)}/skills?session_id=${encodeURIComponent(selected.id)}`);
          if(target.value!==selected.id||!receipt.isConnected)return;
          receipt.replaceChildren(el('p',data.latest?`${data.latest.skills.length} skills in this session’s recorded snapshot`:'No recorded skill snapshot; inspect this session before connecting.','small muted'));
          for(const skill of data.latest?.skills||[])receipt.append(el('p',`${skill.name} · ${skill.hash.slice(0,12)}`,'small'));
        }),receipt);
        const submit=el('button','Attach existing session');submit.type='submit';form.append(submit);
        form.onsubmit=async event=>{event.preventDefault();submit.disabled=true;try{
          await api(`/api/sessions/${session.id}/connections/attach`,{session_id:target.value,purpose:purpose.value,expected_version:graph.version,dependencies:[{source_id:session.id,readiness:readiness.value}]});
          message('Existing session connected. Its process and skill snapshot are preserved.');await load(root,session,onDelivered);
        }catch(error){message(error.message);}finally{submit.disabled=false;}};
        const attach=el('details');attach.append(el('summary','Attach an existing session'),form);root.append(attach);
      }
    }catch(error){root.replaceChildren(el('p',error.message),button('Retry loading connections',()=>load(root,session,onDelivered)));}
  }
  return {load};
}
