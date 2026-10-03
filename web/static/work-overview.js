const attentionNames={normal:'None',needs_input:'Needs input',blocked:'Blocked',ready_for_review:'Needs review'};
export function setupOverview({state,el,openCreate,api,message,refresh}) {
  const $=selector=>document.querySelector(selector),keys=['project','profile','tool','mechanical','attention','result','scope'];
  let preferences={};try{const stored=JSON.parse(localStorage.getItem('workbench-filters')||'{}');if(stored&&typeof stored==='object'&&!Array.isArray(stored))preferences=stored;}catch{}
  function keepFocus(root){const active=document.activeElement;if(!root.contains(active))return()=>{};const href=active.getAttribute('href'),text=active.textContent,node=active.closest('[data-node]')?.dataset.node;return()=>{if(document.activeElement===document.body)[...root.querySelectorAll('a,button,summary')].find(n=>n.getAttribute('href')===href&&n.textContent===text&&n.closest('[data-node]')?.dataset.node===node)?.focus({preventScroll:true});};}
  function creation(node){return node.created_at||sessionsById.get(node.native_id)?.created_at||'';}
  function stableOrder(a,b){return creation(a).localeCompare(creation(b))||a.id.localeCompare(b.id);}
  // Reconcile tree rows and work cards in place so controls retain identity across polling.
  function reconcile(old,next){
    for(const attr of [...old.attributes])if(!next.hasAttribute(attr.name))old.removeAttribute(attr.name);
    for(const attr of next.attributes)if(old.getAttribute(attr.name)!==attr.value)old.setAttribute(attr.name,attr.value);
    old.onclick=next.onclick;old.ontoggle=next.ontoggle;
    if(!next.children.length){if(old.textContent!==next.textContent)old.textContent=next.textContent;return old;}
    const available=[...old.children];let cursor=old.firstElementChild;
    for(const child of [...next.children]){
      const match=available.find(n=>n.tagName===child.tagName&&n.className===child.className&&n.dataset.node===child.dataset.node);
      const updated=match?reconcile(match,child):child;
      if(match)available.splice(available.indexOf(match),1);
      if(updated!==cursor)old.insertBefore(updated,cursor);
      cursor=updated.nextElementSibling;
    }
    for(const child of available)child.remove();
    return old;
  }
  function save(){const values={search:$('#search').value,show:$('#state-filter').value,includeHidden:$('#include-hidden')?.checked||false};for(const key of keys)values[key]=$('#filter-'+key).value;try{localStorage.setItem('workbench-filters',JSON.stringify(values));}catch{}}
  let indexed=null,nodesById=new Map(),sessionsById=new Map(),membersByRoot=new Map(),childrenByParent=new Map();
  function indexWork(){
    if(indexed===state.work)return;indexed=state.work;
    nodesById=new Map((state.work?.nodes||[]).map(n=>[n.id,n]));
    sessionsById=new Map(state.sessions.map(s=>[s.id,s]));membersByRoot=new Map();childrenByParent=new Map();
    for(const node of state.work?.nodes||[]){
      if(!membersByRoot.has(node.root_id))membersByRoot.set(node.root_id,[]);membersByRoot.get(node.root_id).push(node);
      if(!childrenByParent.has(node.owner_id))childrenByParent.set(node.owner_id,[]);childrenByParent.get(node.owner_id).push(node);
    }
    for(const children of childrenByParent.values())children.sort(stableOrder);
    for(const members of membersByRoot.values())members.sort(stableOrder);
  }
  function nodeFor(session){indexWork();return nodesById.get(state.work?.aliases?.[session.id]||session.id);}
  let renderKey='',historyOpen=null,historyLimit=20,activeLimit=20,searchTimer;
  const pending=new Set();
  // A polling update can also change card geometry. Finish the current pointer
  // activation before applying it, then reconcile the latest response in place.
  const workList=$('#work-list');let heldPointer=null;
  workList.addEventListener('pointerdown',event=>{heldPointer=event.pointerId;});
  function releasePointer(event){
    if(heldPointer===null||event.pointerId!==undefined&&event.pointerId!==heldPointer)return;
    heldPointer=null;setTimeout(renderWork,0);
  }
  document.addEventListener('pointerup',releasePointer);
  document.addEventListener('pointercancel',releasePointer);
  window.addEventListener('blur',()=>releasePointer({}));
  function changedFilters(){historyOpen=null;historyLimit=activeLimit=20;save();renderWork();}
  async function mutate(node,action){
    indexWork();const session=sessionsById.get(node.native_id);if(!session)return;
    const key=`${session.id}:${action}`;if(pending.has(key))return;
    pending.add(key);renderKey='';renderWork();
    try{
      if(action==='visibility')await api(`/api/workbench/sessions/${encodeURIComponent(session.id)}/visibility`,{hidden:!node.hidden},'PATCH');
      else await api(`/api/sessions/${encodeURIComponent(session.tmux_name)}/attention`,{state:'normal',note:session.attention_note||''},'PATCH');
      message(action==='visibility'?`${node.hidden?'Restored':'Hidden'} ${session.tmux_name}. History and files are kept.`:`Marked ${session.tmux_name} reviewed. Result outcomes are kept.`);
      if(state.loading)await state.loading;await refresh();
    }catch(error){message(`${session.tmux_name}: ${error.message}`);}
    finally{pending.delete(key);renderKey='';renderWork();}
  }
  function link(node,results=false){return node.native_name?`#${results?'results':'session'}/${encodeURIComponent(node.native_name)}`:`#step/${encodeURIComponent(node.id)}`;}
  function statuses(node){
    const wrap=el('div',null,'work-statuses');
    for(const text of [`Terminal: ${node.mechanical}`,`Attention: ${attentionNames[node.attention]||node.attention}`,`Result: ${node.result_state}`])wrap.append(el('span',text,'badge'));
    if(node.readiness?.stale)wrap.append(el('span','Inputs changed','badge attention'));
    if(node.release)wrap.append(el('span',`Release: ${node.release.state}`,'badge'+(node.release.state==='unknown'?' attention':'')));
    if(node.waiting)wrap.append(el('span',node.decision==='proposed'?'Proposed':'Waiting for inputs or capacity','badge'));
    return wrap;
  }
  function filterOptions(){
    const nodes=state.work?.nodes||[];
    for(const [key,field] of [['project','project_id'],['profile','profile'],['tool','tool']]){
      const select=$('#filter-'+key),current=select.dataset.initialized?select.value:preferences[key]||'';
      const values=[...new Set(nodes.map(n=>n[field]).filter(Boolean))].sort();
      if(current&&!values.includes(current))values.push(current);
      const choices=[['','Any'],...values.map(value=>[value,key==='project'?(nodes.find(n=>n.project_id===value)?.project_name||value):value])];
      if(select.dataset.options!==JSON.stringify(choices)){
        select.replaceChildren(...choices.map(([value,label])=>{const o=el('option',label);o.value=value;return o;}));select.dataset.options=JSON.stringify(choices);
      }
      select.value=current;select.dataset.initialized='true';
    }
  }
  function matches(node){
    const query=$('#search').value.toLowerCase().trim();
    if(query&&!`${node.title} ${node.task} ${node.repository} ${node.project_name||''}`.toLowerCase().includes(query))return false;
    const fields={project:'project_id',profile:'profile',tool:'tool',mechanical:'mechanical',attention:'attention',result:'result_state'};
    return Object.entries(fields).every(([key,field])=>!$('#filter-'+key).value||node[field]===$('#filter-'+key).value);
  }
  function renderWork(){
    if(!state.work)return;
    // Settings is also a direct route; readiness must not depend on visiting Work first.
    const readiness=state.work.readiness;
    const warningsKey=JSON.stringify(readiness.warnings),readinessPanel=$('#readiness-summary').parentElement;
    if(readiness.warnings.length&&readinessPanel.dataset.warnings!==warningsKey)readinessPanel.open=true;
    readinessPanel.dataset.warnings=warningsKey;
    $('#readiness-summary').textContent=readiness.ready?'System ready':`${readiness.warnings.length} readiness warning${readiness.warnings.length===1?'':'s'}`;
    $('#readiness-details').replaceChildren(...readiness.warnings.map(w=>el('p',w)));
    if($('#work-view').hidden||heldPointer!==null)return;
    indexWork();filterOptions();
    const {nodes,groups}=state.work,root=workList.cloneNode(false);
    const includeHidden=$('#include-hidden')?.checked||false,show=$('#state-filter').value,allNodes=$('#filter-scope').value==='all';
    const query=$('#search').value.trim(),filtered=query||keys.some(k=>k!=='scope'&&$('#filter-'+k).value);
    const expanded=historyOpen??(show!=='active'||!!filtered);
    const key=JSON.stringify([nodes,groups,readiness.ready,readiness.warnings,query,show,keys.map(k=>$('#filter-'+k).value),includeHidden,expanded,historyLimit,activeLimit,[...pending]]);
    if(key===renderKey)return;renderKey=key;
    const restoreFocus=keepFocus(workList);
    const visible=node=>includeHidden||!node.hidden||node.mechanical==='running'||node.waiting;
    const strip=$('#attention-strip');strip.replaceChildren();
    const attention=nodes.filter(n=>visible(n)&&n.needs_attention);
    if(attention.length){
      const a=el('a',`${attention.length} session${attention.length===1?' needs':'s need'} attention`);a.href='#work';
      a.onclick=()=>{$('#state-filter').value='attention';changedFilters();};strip.append(a);
    }
    if(readiness.warnings.length){const a=el('a',`${readiness.warnings.length} readiness warning${readiness.warnings.length===1?'':'s'}`);a.href='#settings';strip.append(a);}
    strip.hidden=!strip.childElementCount;
    const entries=[];
    for(const group of groups){
      const members=membersByRoot.get(group.root_id)||[],eligible=members.filter(n=>visible(n)&&matches(n));
      if(!eligible.length)continue;
      for(const node of allNodes?eligible:[nodesById.get(group.root_id)]){
        if(!node)continue;
        const visibleMembers=allNodes?[node]:members.filter(visible);
        const active=visibleMembers.some(n=>n.mechanical==='running'||n.waiting);
        const priority=visibleMembers.some(n=>n.needs_attention)?0:visibleMembers.some(n=>n.mechanical==='running')?1:visibleMembers.some(n=>n.waiting)?2:3;
        if(show==='attention'&&priority!==0)continue;
        entries.push({node,group,members:visibleMembers,eligible,active,priority});
      }
    }
    entries.sort((a,b)=>creation(nodesById.get(b.group.root_id)).localeCompare(creation(nodesById.get(a.group.root_id)))||a.group.root_id.localeCompare(b.group.root_id)||stableOrder(a.node,b.node));
    const current=entries.filter(e=>e.active),history=entries.filter(e=>!e.active),sections=new Map();
    function appendCard(entry,target){
      const {node,group,members,eligible}=entry;
      const card=el('article',null,'card');card.dataset.node=node.id;
      card.append(el('h3',node.title.length>100?node.title.slice(0,97)+'…':node.title),statuses(node));
      if(node.hidden)card.append(el('p','Hidden session · shown as context for its children','small muted'));
      if(node.task)card.append(el('p',node.task.slice(0,500),'brief muted'));
      card.append(el('p',`${node.project_name?node.project_name+' · ':''}${node.repository||'No repository'}`,'overview-meta'),el('p',`${node.profile||'Session'} · ${node.tool||'Terminal'}${node.tool&&node.tool!=='shell'?' / '+(node.model||'Default model (not recorded)'):''} · ${node.last_activity?new Date(node.last_activity).toLocaleString():'No activity recorded'}`,'overview-meta'));
      if(!allNodes&&group.children_total)card.append(el('p',`${group.children_complete}/${group.children_total} children complete${members.some(n=>n.id!==node.id&&n.mechanical==='running')?' · child running':''}`,'small'));
      const matched=!allNodes&&query?eligible.filter(n=>n.id!==node.id):[];
      if(matched.length){
        const matches=el('div',null,'matched-children');matches.append(el('small',`${matched.length} matching child session${matched.length===1?'':'s'}`));
        for(const child of matched.slice(0,5)){const a=el('a',child.title);a.href=link(child);matches.append(a);}
        if(matched.length>5)matches.append(el('small',`${matched.length-5} more matches in the session tree`));card.append(matches);
      }
      const recent=allNodes?node:members.filter(n=>n.result).sort((a,b)=>b.result.created_at.localeCompare(a.result.created_at))[0];
      if(recent?.result){const result=el('div',null,'recent-result');result.append(el('strong',`${recent.result.kind==='final'?'Final':'Ready'} · ${recent.result.outcome}`),el('p',recent.result.summary.slice(0,300),'brief'));card.append(result);}
      const focus=allNodes?node:members.find(n=>n.needs_attention)||members.find(n=>n.mechanical==='running')||members.find(n=>n.waiting)||matched[0]||node;
      const actions=el('div',null,'work-card-actions'),open=el('a','Open work','button');open.href=link(node);actions.append(open);
      if(focus.result||focus.decision){const resultLink=el('a',focus.result_state==='failed'?'Review failure':focus.decision==='proposed'?'Review next step':'Results','button');resultLink.href=link(focus,true);actions.append(resultLink);}
      const native=sessionsById.get(node.native_id);
      if(native&&!native.running){const button=el('button',node.hidden?'Restore':'Hide');button.disabled=pending.has(`${native.id}:visibility`);button.onclick=()=>mutate(node,'visibility');button.title='Keep all history and files; only change list visibility';actions.append(button);}
      const reviewNative=sessionsById.get(focus.native_id);
      if(focus.reviewable&&reviewNative?.managed&&reviewNative.execution_kind!=='integration-plan'){
        const button=el('button','Mark reviewed');button.disabled=pending.has(`${reviewNative.id}:review`);button.onclick=()=>mutate(focus,'review');actions.append(button);
      }
      card.append(actions);target.append(card);
    }
    for(const entry of current.slice(0,activeLimit)){
      if(!sections.has('active')){const section=el('section',null,'work-section'),cards=el('div',null,'cards');section.append(el('h2','Active work'),cards);root.append(section);sections.set('active',cards);}
      appendCard(entry,sections.get('active'));
    }
    if(current.length>activeLimit){const more=el('button',`Show more active work (${current.length-activeLimit} remaining)`);more.onclick=()=>{activeLimit+=20;renderWork();};root.append(more);}
    if(history.length){
      const panel=el('details',null,'work-history'),count=history.reduce((n,e)=>n+e.members.filter(m=>m.needs_attention).length,0);
      panel.id='work-history';panel.open=expanded;
      panel.append(el('summary',`History · ${history.length} ${allNodes?'sessions':'work groups'}${count?' · '+count+' need attention':''}`));
      panel.ontoggle=event=>{const open=event.currentTarget.open;if(open!==(historyOpen??(show!=='active'||!!filtered))){historyOpen=open;renderWork();}};
      if(expanded){const cards=el('div',null,'cards');for(const entry of history.slice(0,historyLimit))appendCard(entry,cards);panel.append(cards);
        if(history.length>historyLimit){const more=el('button',`Show more history (${history.length-historyLimit} remaining)`);more.onclick=()=>{historyLimit+=20;renderWork();};panel.append(more);}}
      root.append(panel);
    }
    if(!entries.length)root.append(el('p',nodes.length?'No work matches these filters. Include hidden to find sessions you have hidden.':'Your workspace is ready. Start a session to begin.','empty'));
    reconcile(workList,root);
    restoreFocus();
  }
  let collapsedBranches=new Set(),lastSelection=null;
  try{const saved=JSON.parse(localStorage.getItem('workbench-collapsed-branches')||'[]');if(Array.isArray(saved))collapsedBranches=new Set(saved.filter(x=>typeof x==='string'));}catch{}
  function renderTree(session,selectedId){
    const node=nodeFor(session);if(!node)return;
    const tree=$('#session-tree'),restoreFocus=keepFocus(tree),expanded=new Set([...tree.querySelectorAll('details[open]')].map(d=>d.dataset.node));
    indexWork();const members=membersByRoot.get(node.root_id)||[],root=nodesById.get(node.root_id),items=[],visited=new Set();
    const selection=selectedId||node.id,shown=new Set();
    for(const member of members)if($('#include-hidden')?.checked||!member.hidden||member.id===selection){
      let current=member;const seen=new Set();
      while(current&&!seen.has(current.id)){seen.add(current.id);shown.add(current.id);current=nodesById.get(current.owner_id);}
    }
    if(lastSelection!==selection){
      const seen=new Set();let ancestor=nodesById.get(selection);
      while(ancestor&&!seen.has(ancestor.id)){seen.add(ancestor.id);collapsedBranches.delete(ancestor.owner_id);ancestor=nodesById.get(ancestor.owner_id);}
      lastSelection=selection;
    }
    function visit(current,depth){
      if(!current||!shown.has(current.id)||visited.has(current.id))return;visited.add(current.id);
      const item=el('div',null,`node${current.id===(selectedId||node.id)?' active':''}`);item.style.setProperty('--depth',depth);item.dataset.node=current.id;
      const a=el('a',current.title.length>80?current.title.slice(0,77)+'…':current.title);a.href=link(current);if(current.id===(selectedId||node.id))a.setAttribute('aria-current','page');
      const children=(childrenByParent.get(current.id)||[]).filter(n=>shown.has(n.id)),heading=el('div',null,'node-heading');
      heading.append(a);if(current.hidden)heading.append(el('small','Hidden','muted'));
      if(children.length){
        const toggle=el('button',collapsedBranches.has(current.id)?'Expand children':'Collapse children','branch-toggle');
        toggle.dataset.icon=collapsedBranches.has(current.id)?'chevron-right':'chevron-down';toggle.setAttribute('data-icon-only','');
        toggle.setAttribute('aria-label',`Toggle children of ${current.title}`);toggle.setAttribute('aria-expanded',String(!collapsedBranches.has(current.id)));
        toggle.onclick=()=>{if(collapsedBranches.has(current.id))collapsedBranches.delete(current.id);else collapsedBranches.add(current.id);try{localStorage.setItem('workbench-collapsed-branches',JSON.stringify([...collapsedBranches].slice(-500)));}catch{}renderTree(session,selectedId);[...tree.querySelectorAll('.branch-toggle')].find(b=>b.closest('[data-node]').dataset.node===current.id)?.focus({preventScroll:true});};
        heading.append(toggle);
      }
      item.append(heading,el('small',`${current.profile||'Session'} · ${current.attempt_state||current.mechanical} · result ${current.result_state}`));
      const native=sessionsById.get(current.native_id);
      if(!native||native.managed&&native.execution_kind!=='integration-plan'){
        const add=el('button','+ Add session','add-session');add.onclick=()=>openCreate(native||{id:current.id,tmux_name:current.title,tool:current.tool,profile:current.profile,repository:current.repository});item.append(add);
      }
      if(current.attempts.length>1){const history=el('details');history.dataset.node=current.id;history.open=expanded.has(current.id);history.append(el('summary',`${current.attempts.length} attempts`));for(const attempt of [...current.attempts].reverse()){
        const link=el('a',`Attempt ${attempt.generation} · ${attempt.state}${attempt.result?' · '+attempt.result.outcome:''}`);link.href=`#session/${encodeURIComponent(attempt.name)}`;history.append(link);
      }item.append(history);}
      items.push(item);if(!collapsedBranches.has(current.id))children.forEach(child=>visit(child,depth+1));
    }
    visit(root,0);
    const scrollTop=tree.scrollTop,scrollLeft=tree.scrollLeft,existing=new Map([...tree.children].map(item=>[item.dataset.node,item]));
    const retained=new Set();let cursor=tree.firstElementChild;
    for(const item of items){const old=existing.get(item.dataset.node);const row=old?reconcile(old,item):item;retained.add(row);if(row!==cursor)tree.insertBefore(row,cursor);cursor=row.nextElementSibling;}
    for(const old of existing.values())if(!retained.has(old))old.remove();
    tree.scrollTop=scrollTop;tree.scrollLeft=scrollLeft;restoreFocus();
  }
  $('#search').value=preferences.search||'';$('#state-filter').value=preferences.show||'active';
  for(const key of keys){const select=$('#filter-'+key);if(!['project','profile','tool'].includes(key))select.value=preferences[key]||'';select.onchange=changedFilters;}
  $('#search').oninput=()=>{clearTimeout(searchTimer);searchTimer=setTimeout(changedFilters,150);};$('#state-filter').onchange=changedFilters;
  if($('#include-hidden')){$('#include-hidden').checked=preferences.includeHidden===true;$('#include-hidden').onchange=changedFilters;}
  $('#reset-filters').onclick=()=>{preferences={};$('#search').value='';$('#state-filter').value='active';for(const key of keys)$('#filter-'+key).value='';if($('#include-hidden'))$('#include-hidden').checked=false;changedFilters();};
  $('#session-tree').onkeydown=event=>{
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
    const targets=[...$('#session-tree').querySelectorAll('a,button,summary')].filter(n=>n.getClientRects().length),index=targets.indexOf(document.activeElement);if(index<0)return;
    event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?targets.length-1:Math.max(0,Math.min(targets.length-1,index+(event.key==='ArrowDown'?1:-1)));targets[next].focus();
  };
  return {renderWork,renderTree,nodeFor,link,statuses};
}
