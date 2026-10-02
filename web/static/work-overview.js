const attentionNames={normal:'None',needs_input:'Needs input',blocked:'Blocked',ready_for_review:'Needs review'};
export function setupOverview({state,el,openCreate}) {
  const $=selector=>document.querySelector(selector),keys=['project','profile','tool','mechanical','attention','result','scope'];
  let preferences={};try{const stored=JSON.parse(localStorage.getItem('workbench-filters')||'{}');if(stored&&typeof stored==='object'&&!Array.isArray(stored))preferences=stored;}catch{}
  function keepFocus(root){const active=document.activeElement;if(!root.contains(active))return()=>{};const href=active.getAttribute('href'),text=active.textContent,node=active.closest('[data-node]')?.dataset.node;return()=>{if(document.activeElement===document.body)[...root.querySelectorAll('a,button,summary')].find(n=>n.getAttribute('href')===href&&n.textContent===text&&n.closest('[data-node]')?.dataset.node===node)?.focus({preventScroll:true});};}
  function save(){const values={search:$('#search').value,show:$('#state-filter').value};for(const key of keys)values[key]=$('#filter-'+key).value;try{localStorage.setItem('workbench-filters',JSON.stringify(values));}catch{}}
  function nodeFor(session){return state.work?.nodes.find(n=>n.id===(state.work.aliases[session.id]||session.id));}
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
    filterOptions();const {nodes,groups,readiness}=state.work;const root=$('#work-list'),restoreFocus=keepFocus(root);root.replaceChildren();
    const strip=$('#attention-strip');strip.replaceChildren();
    const attention=nodes.filter(n=>n.needs_attention);
    if(attention.length){const a=el('a',`${attention.length} session${attention.length===1?' needs':'s need'} attention`);a.href=link(attention[0],!!attention[0].result||!!attention[0].decision);strip.append(a);}
    if(readiness.warnings.length){const a=el('a',`${readiness.warnings.length} readiness warning${readiness.warnings.length===1?'':'s'}`);a.href='#settings';strip.append(a);}
    strip.hidden=!strip.childElementCount;
    const sections=new Map(),show=$('#state-filter').value,allNodes=$('#filter-scope').value==='all';
    function appendCard(node,group){
      if(!sections.has(group.priority)){const section=el('section',null,'work-section'),cards=el('div',null,'cards');section.append(el('h2',['Needs attention','Running','Waiting','Recent'][group.priority]),cards);root.append(section);sections.set(group.priority,cards);}
      const card=el('article',null,'card'),members=nodes.filter(n=>group.member_ids.includes(n.id));card.dataset.node=node.id;
      const title=el('h3',node.title.length>100?node.title.slice(0,97)+'…':node.title);card.append(title,statuses(node));
      if(node.task)card.append(el('p',node.task,'brief muted'));
      card.append(el('p',`${node.project_name?node.project_name+' · ':''}${node.repository||'No repository'}`,'overview-meta'),el('p',`${node.profile||'Session'} · ${node.tool||'Terminal'}${node.tool&&node.tool!=='shell'?' / '+(node.model||'Default model (not recorded)'):''} · ${node.last_activity?new Date(node.last_activity).toLocaleString():'No activity recorded'}`,'overview-meta'));
      if(!allNodes&&group.children_total)card.append(el('p',`${group.children_complete}/${group.children_total} children complete${members.some(n=>n.id!==node.id&&n.mechanical==='running')?' · child running':''}`,'small'));
      const recent=allNodes?node:members.filter(n=>n.result).sort((a,b)=>b.result.created_at.localeCompare(a.result.created_at))[0];
      if(recent?.result){const result=el('div',null,'recent-result');result.append(el('strong',`${recent.result.kind==='final'?'Final':'Ready'} · ${recent.result.outcome}`),el('p',recent.result.summary,'brief'));card.append(result);}
      const focus=allNodes?node:members.find(n=>n.needs_attention)||node;
      const actions=el('div',null,'work-card-actions'),open=el('a','Open work','button');open.href=link(focus);actions.append(open);
      if(focus.result||focus.decision){const resultLink=el('a',focus.result_state==='failed'?'Review failure':focus.decision==='proposed'?'Review next step':'Results','button');resultLink.href=link(focus,true);actions.append(resultLink);}
      card.append(actions);sections.get(group.priority).append(card);
    }
    const entries=[];
    for(const group of groups){
      const members=nodes.filter(n=>group.member_ids.includes(n.id));
      const eligible=members.filter(matches);if(!eligible.length)continue;
      for(const node of allNodes?eligible:[nodes.find(n=>n.id===group.root_id)]){
        const view=allNodes?{...group,priority:node.needs_attention?0:node.mechanical==='running'?1:node.waiting?2:3,last_activity:node.last_activity}:group;
        entries.push({node,group:view});
      }
    }
    entries.sort((a,b)=>b.group.last_activity.localeCompare(a.group.last_activity));entries.sort((a,b)=>a.group.priority-b.group.priority);
    let recentCount=0;
    for(const {node,group} of entries){
      if(show==='attention'&&group.priority!==0)continue;
      if(show==='active'&&group.priority===3&&recentCount++>=12)continue;
      appendCard(node,group);
    }
    if(!root.childElementCount)root.append(el('p',nodes.length?'No work matches these filters.':'Your staging workspace is ready. Start a session to begin.','empty'));
    const warningsKey=JSON.stringify(readiness.warnings),readinessPanel=$('#readiness-summary').parentElement;
    if(readiness.warnings.length&&readinessPanel.dataset.warnings!==warningsKey)readinessPanel.open=true;
    readinessPanel.dataset.warnings=warningsKey;
    $('#readiness-summary').textContent=readiness.ready?'System ready':`${readiness.warnings.length} readiness warning${readiness.warnings.length===1?'':'s'}`;
    $('#readiness-details').replaceChildren(...readiness.warnings.map(w=>el('p',w)));
    restoreFocus();
  }
  let collapsedBranches=new Set(),lastSelection=null;
  try{const saved=JSON.parse(localStorage.getItem('workbench-collapsed-branches')||'[]');if(Array.isArray(saved))collapsedBranches=new Set(saved.filter(x=>typeof x==='string'));}catch{}
  function renderTree(session,selectedId){
    const node=nodeFor(session);if(!node)return;
    const tree=$('#session-tree'),restoreFocus=keepFocus(tree),expanded=new Set([...tree.querySelectorAll('details[open]')].map(d=>d.dataset.node));
    const members=state.work.nodes.filter(n=>n.root_id===node.root_id),root=members.find(n=>n.id===node.root_id),items=[],visited=new Set();
    const selection=selectedId||node.id;
    if(lastSelection!==selection){
      const seen=new Set();let ancestor=members.find(n=>n.id===selection);
      while(ancestor&&!seen.has(ancestor.id)){seen.add(ancestor.id);collapsedBranches.delete(ancestor.owner_id);ancestor=members.find(n=>n.id===ancestor.owner_id);}
      lastSelection=selection;
    }
    function visit(current,depth){
      if(visited.has(current.id))return;visited.add(current.id);
      const item=el('div',null,`node${current.id===(selectedId||node.id)?' active':''}`);item.style.setProperty('--depth',depth);item.dataset.node=current.id;
      const a=el('a',current.title.length>80?current.title.slice(0,77)+'…':current.title);a.href=link(current);if(current.id===(selectedId||node.id))a.setAttribute('aria-current','page');
      const children=members.filter(n=>n.owner_id===current.id),heading=el('div',null,'node-heading');
      heading.append(a);
      if(children.length){
        const toggle=el('button',collapsedBranches.has(current.id)?'▸':'▾','branch-toggle');
        toggle.setAttribute('aria-label',`Toggle children of ${current.title}`);toggle.setAttribute('aria-expanded',String(!collapsedBranches.has(current.id)));
        toggle.onclick=()=>{if(collapsedBranches.has(current.id))collapsedBranches.delete(current.id);else collapsedBranches.add(current.id);try{localStorage.setItem('workbench-collapsed-branches',JSON.stringify([...collapsedBranches].slice(-500)));}catch{}renderTree(session,selectedId);[...tree.querySelectorAll('.branch-toggle')].find(b=>b.closest('[data-node]').dataset.node===current.id)?.focus({preventScroll:true});};
        heading.append(toggle);
      }
      item.append(heading,el('small',`${current.profile||'Session'} · ${current.attempt_state||current.mechanical} · result ${current.result_state}`));
      const native=state.sessions.find(s=>s.id===current.native_id);
      if(!native||native.managed&&native.execution_kind!=='integration-plan'){
        const add=el('button','+ Add session','add-session');add.onclick=()=>openCreate(native||{id:current.id,tmux_name:current.title,tool:current.tool,profile:current.profile,repository:current.repository});item.append(add);
      }
      if(current.attempts.length>1){const history=el('details');history.dataset.node=current.id;history.open=expanded.has(current.id);history.append(el('summary',`${current.attempts.length} attempts`));for(const attempt of [...current.attempts].reverse()){
        const link=el('a',`Attempt ${attempt.generation} · ${attempt.state}${attempt.result?' · '+attempt.result.outcome:''}`);link.href=`#session/${encodeURIComponent(attempt.name)}`;history.append(link);
      }item.append(history);}
      items.push(item);if(!collapsedBranches.has(current.id))children.forEach(child=>visit(child,depth+1));
    }
    visit(root,0);tree.replaceChildren(...items);restoreFocus();
  }
  $('#search').value=preferences.search||'';$('#state-filter').value=preferences.show||'active';
  for(const key of keys){const select=$('#filter-'+key);if(!['project','profile','tool'].includes(key))select.value=preferences[key]||'';select.onchange=()=>{save();renderWork();};}
  $('#search').oninput=()=>{save();renderWork();};$('#state-filter').onchange=()=>{save();renderWork();};
  $('#reset-filters').onclick=()=>{preferences={};$('#search').value='';$('#state-filter').value='active';for(const key of keys)$('#filter-'+key).value='';save();renderWork();};
  $('#session-tree').onkeydown=event=>{
    if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;
    const targets=[...$('#session-tree').querySelectorAll('a,button,summary')].filter(n=>n.getClientRects().length),index=targets.indexOf(document.activeElement);if(index<0)return;
    event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?targets.length-1:Math.max(0,Math.min(targets.length-1,index+(event.key==='ArrowDown'?1:-1)));targets[next].focus();
  };
  return {renderWork,renderTree,nodeFor,link,statuses};
}
