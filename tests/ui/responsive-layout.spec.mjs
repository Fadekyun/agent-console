import {test,expect} from '@playwright/test';

async function fixture(page){
  const title='session-'+('long_unbroken_title_').repeat(10);
  const repository='/workspace/'+('long_repository_segment_').repeat(12);
  const sessions=Array.from({length:11},(_,depth)=>({id:`node-${depth}`,tmux_name:depth?`child-${depth}-${title}`:title,parent_session_id:depth?`node-${depth-1}`:null,created_at:`2026-10-03T00:${String(depth).padStart(2,'0')}:00Z`,last_activity:'2026-10-03T00:00:00Z',tool:'shell',profile:'coder',repository,initial_task:'Review the layout and preserve session identity.',running:true,managed:true,attention_state:'normal',actions:['attach','interrupt','kill']}));
  let reads=0;
  await page.route('**/api/**',async route=>{
    const path=new URL(route.request().url()).pathname;let body={};
    if(path==='/api/me')body={login:'long_identity_without_spaces_'.repeat(4),access_surface:'local-lan',default_tool:'shell',profiles:[{name:'coder',display_name:'Coder',read_write_capability:'write',status:'active'}],tool_status:[{name:'shell',status:'ready'}],auth_contexts:[{tool:'shell',name:'default',provider:'local',status:'ready'}]};
    else if(path==='/api/interface')body={label:'Independent layout fixture'};
    else if(path==='/api/sessions')body=sessions;
    else if(path==='/api/workbench'){
      reads++;const nodes=sessions.map(s=>({id:s.id,owner_id:s.parent_session_id,native_id:s.id,native_name:s.tmux_name,root_id:'node-0',title:s.tmux_name,task:s.initial_task,repository:s.repository,created_at:s.created_at,last_activity:s.last_activity,tool:s.tool,profile:s.profile,mechanical:s.running?'running':'stopped',attention:s.attention_state,result_state:'unknown',needs_attention:s.attention_state!=='normal',waiting:false,attempts:[],readiness:{},hidden:false}));
      body={sessions,nodes,groups:[{root_id:'node-0',member_ids:nodes.map(n=>n.id),priority:1,last_activity:sessions[0].last_activity,children_total:10,children_complete:0}],aliases:{},readiness:{ready:true,warnings:[]}};
    }else if(['/api/plans','/api/projects','/api/session-groups'].includes(path))body=[];
    else if(path.endsWith('/brief'))body={brief:''};
    else if(path.endsWith('/review'))body={content:'Fixture output'};
    else if(path==='/api/skills/effective')body={effective:[],issues:[]};
    await route.fulfill({json:body});
  });
  await page.routeWebSocket('**/ws/sessions/**',ws=>ws.send('Fixture connected\r\n'));
  return{sessions,title,get reads(){return reads;}};
}
async function insideViewport(locator,width){
  for(const node of await locator.all()){
    const box=await node.boundingBox();if(!box)continue;
    expect(box.x).toBeGreaterThanOrEqual(-1);expect(box.x+box.width).toBeLessThanOrEqual(width+1);
  }
}
async function refresh(page,f){const before=f.reads;await page.evaluate(()=>window.dispatchEvent(new Event('focus')));await expect.poll(()=>f.reads).toBeGreaterThan(before);}

test.describe('responsive content geometry',()=>{
  test.skip(({isMobile})=>isMobile,'Explicit five-width matrix runs once per browser');
  for(const width of [320,360,390,768,1280]){
    test(`${width}px Work controls, settings actions and create dialog retain usable spacing`,async({page})=>{
      await page.setViewportSize({width,height:800});await fixture(page);await page.goto('/work');await expect(page.locator('.card')).toHaveCount(1);
      await insideViewport(page.locator('.card,.filter-row input,.filter-row select,.filter-row button'),width);
      if(width<=400)expect((await page.locator('#search').boundingBox()).width).toBeGreaterThanOrEqual(width-40);
      await page.locator('#new-session').click();await expect(page.locator('#create-dialog')).toBeVisible();await insideViewport(page.locator('#create-dialog input:visible,#create-dialog select:visible,#create-dialog textarea:visible,#create-dialog button:visible'),width);
      await expect(page.locator('#role-description')).toBeHidden();await expect(page.locator('#create-error')).toBeHidden();await page.locator('#cancel-create').click();
      await page.goto('/work#settings');const link=page.locator('#settings-view a.button[href="/environment"]');await link.scrollIntoViewIfNeeded();expect((await link.boundingBox()).height).toBeGreaterThanOrEqual(44);await insideViewport(link,width);
      const panel=await page.locator('#settings-view>.panel').boundingBox(),box=await link.boundingBox();expect(box.x).toBeGreaterThan(panel.x);expect(box.x+box.width).toBeLessThan(panel.x+panel.width);
    });
    test(`${width}px long mobile session names keep every card action on screen`,async({page})=>{
      await page.setViewportSize({width,height:800});await fixture(page);await page.goto('/mobile');await expect(page.locator('.mobile-session')).toHaveCount(11);
      await insideViewport(page.locator('.mobile-header,.mobile-session,.mobile-session-actions button,.mobile-session-actions a'),width);
      await expect(page.locator('.mobile-session').first().getByRole('button',{name:'Details',exact:true})).toBeVisible();
      if(width<=480){
        const labels=await page.locator('.mobile-tabs button').evaluateAll(buttons=>buttons.map(button=>{const range=document.createRange();range.selectNodeContents(button);const text=range.getBoundingClientRect(),box=button.getBoundingClientRect();return text.left>=box.left&&text.right<=box.right;}));
        expect(labels.every(Boolean)).toBe(true);
      }
      expect(await page.locator('.mobile-session').first().evaluate(e=>e.scrollWidth<=e.clientWidth)).toBe(true);
      await page.locator('.mobile-session').first().getByRole('button',{name:'Details',exact:true}).click();await expect(page.locator('#mobile-attention-dialog')).toBeVisible();await insideViewport(page.locator('#mobile-attention-dialog'),width);
    });
    test(`${width}px deep session tree fits without losing full names or controls`,async({page})=>{
      await page.setViewportSize({width,height:800});const f=await fixture(page);await page.goto('/work#session/'+encodeURIComponent(f.title));
      if(await page.locator('#terminal-panel').isHidden())await page.locator('#open-terminal').click();
      if(width<=760)await page.locator('#terminal-sessions').click();
      const tree=page.locator('#session-tree');await expect(tree.locator('.node')).toHaveCount(11);
      const links=tree.locator('.node-heading>a');for(const link of await links.all())expect((await link.boundingBox()).height).toBeLessThanOrEqual(40);
      await expect(links.first()).toHaveAttribute('title',f.title);await expect(links.first()).toHaveAccessibleName(f.title);
      await insideViewport(tree.locator('.node,.node-heading>a,.add-session'),width);
      await tree.locator('[data-node="node-10"]>.add-session').click();await expect(page.locator('#create-dialog')).toBeVisible();await expect(page.locator('#create-form [name=parent]')).toHaveValue('node-10');
    });
  }
});

test('renaming and reordered refresh never duplicate overview cards or tree nodes',async({page})=>{
  const f=await fixture(page);await page.goto('/work');const card=page.locator('.card[data-node="node-0"]'),element=await card.elementHandle();
  f.sessions.find(s=>s.id==='node-0').tmux_name='renamed-root';f.sessions.reverse();await refresh(page,f);
  await expect(page.locator('.card')).toHaveCount(1);await expect(card.locator('h3')).toHaveText('renamed-root');expect(await element.evaluate(e=>e.isConnected)).toBe(true);
  await card.getByRole('link',{name:'Open work',exact:true}).click();await expect(page.locator('#session-title')).toHaveText('renamed-root');
  if(await page.locator('#terminal-panel').isVisible()&&await page.locator('#tree-panel').isHidden())await page.locator('#terminal-sessions').click();
  const tree=page.locator('#session-tree');await expect(tree.locator('.node')).toHaveCount(11);const ids=await tree.locator('.node').evaluateAll(nodes=>nodes.map(n=>n.dataset.node));expect(new Set(ids).size).toBe(11);expect(ids).toEqual(Array.from({length:11},(_,n)=>`node-${n}`));
});
