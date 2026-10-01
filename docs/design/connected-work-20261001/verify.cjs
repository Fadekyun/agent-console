// Run with PLAYWRIGHT_MODULE=/path/to/playwright node verify.cjs.
// Offline fixture checks and the eight reproducible review screenshots.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const {pathToFileURL}=require('node:url');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROMIUM_PATH||undefined});
 const report={browser:await browser.version(),scope:'Offline design fixtures only; no production runtime tested',checks:[],frames:[]};
 const errors=[];
 const page=await browser.newPage({viewport:{width:1440,height:1000}});
 page.on('pageerror',e=>errors.push(e.message));
 await page.route(/^https?:/,route=>{errors.push('Unexpected network: '+route.request().url());return route.abort()});
 const go=(c,f='overview',theme='light')=>page.goto(pathToFileURL(path.join(__dirname,c+'.html')).href+`?frame=${f}&theme=${theme}`);
 const act=a=>page.locator(`[data-action="${a}"]:visible`).first().click();
 for(const c of ['a','b']){
  await go(c);
  await act('proposal');await act('edit-definition');
  await page.locator('#work-title').fill('Improve keyboard search');
  await page.locator('#release-target').selectOption('demo-staging');
  await act('save-definition');
  assert.equal(await page.evaluate(()=>state.target),'demo-preview','saved proposal must not expand active run scope');
  assert.equal(await page.locator('#release-target').inputValue(),'demo-staging');
  await act('start');
  assert.equal(await page.locator('h1').textContent(),'Improve keyboard search');
  await page.locator('[data-tab="Task"]').click();
  assert.match(await page.locator('.inspector-body').textContent(),/demo-staging/);
  await page.locator('[data-tab="Inputs"]').click();
  await page.locator('#draft').fill('Preserve my unfinished message.');
  const order=await page.locator('.work-item strong').allTextContents();
  await page.locator('.terminal-lines').first().evaluate(el=>el.scrollTop=40);
  const scroll=await page.locator('.terminal-lines').first().evaluate(el=>el.scrollTop);
  await page.evaluate(()=>{document.getElementById('draft').focus();document.getElementById('draft').setSelectionRange(4,10);update()});
  assert.equal(await page.locator('#draft').inputValue(),'Preserve my unfinished message.');
  assert.deepEqual(await page.evaluate(()=>[document.activeElement.id,document.activeElement.selectionStart,document.activeElement.selectionEnd]),['draft',4,10]);
  assert.deepEqual(await page.locator('.work-item strong').allTextContents(),order);
  assert.equal(await page.locator('.terminal-lines').first().evaluate(el=>el.scrollTop),scroll);
  assert.match(await page.locator('.notice').textContent(),/Review invalidated; release blocked/);
  await page.evaluate(()=>update()); // coalesce two upstream changes
  await act('deliver');
  assert.match(await page.locator('.inspector-body').textContent(),/Yes · waiting/);
  assert.match(await page.locator('.inspector-body').textContent(),/No · v1 in use/);
  await act('pause');await act('finish');
  assert.equal(await page.evaluate(()=>state.reruns),0);
  await act('pause');
  assert.deepEqual(await page.evaluate(()=>[state.consumed,state.reruns,state.queued]),[3,1,false]);
  await act('fail');assert.match(await page.locator('.inspector-body').textContent(),/Delivery failed/);
  await act('retry');assert.doesNotMatch(await page.locator('.inspector-body').textContent(),/Delivery failed/);
  await act('attach');
  await page.locator('#attach-session').selectOption('unsupported');
  assert.equal(await page.locator('[data-action="confirm-attach"]').isDisabled(),true);
  await page.locator('#attach-session').selectOption('supported');await act('confirm-attach');
  assert.match(await page.locator('.pane-title').first().textContent(),/EXISTING SESSION ATTACHED/);
  await act('stop');await act('confirm-stop');
  assert.match(await page.locator('.notice').textContent(),/Stopped/);
  assert.equal(await page.locator('#draft').inputValue(),'Preserve my unfinished message.');
  report.checks.push(`${c.toUpperCase()}: proposal/edit/start, scope target, draft/focus/selection/scroll/order preservation, coalesced rerun, pause/resume, delivery vs consumption, failure/retry, capability-gated attachment, stop`);
  // Semantic keyboard controls and group movement; nothing requires dragging.
  await go(c,'connected');
  if(c==='a'){
   await page.locator('#group').focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');
   assert.equal(await page.locator('#group').inputValue(),'Later');
  }
  await page.locator('[data-mode="Type"]').focus();await page.keyboard.press('Enter');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'draft');
  await page.locator('#draft').fill('keyboard message');await act('send');
  assert.equal(await page.locator('#draft').inputValue(),'');
  report.checks.push(`${c.toUpperCase()}: keyboard activation, composer and movement without drag`);
  for(const width of [1440,1024,390]){
   await page.setViewportSize({width,height:1000});
   for(const theme of ['light','dark']){
    await go(c,width===390?'phone':'connected',theme);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,`${c} ${width} ${theme} overflow`);
    await page.locator('[data-mode="Type"]').click();
    assert.equal(await page.evaluate(()=>document.activeElement.id),'draft');
    await page.locator('[data-mode="Scroll"]').click();
   }
   report.checks.push(`${c.toUpperCase()}: ${width}px light/dark, no horizontal overflow, explicit touch mode controls`);
  }
  for(const [i,f] of ['overview','connected','changed','phone'].entries()){
   await page.setViewportSize({width:f==='phone'?390:1440,height:f==='phone'?844:1000});
   await go(c,f,c==='b'&&f==='changed'?'dark':'light');
   const file=`${c.toUpperCase()}${i+1}-${f}.png`;
   await page.screenshot({path:path.join(__dirname,'frames',file),fullPage:true});
   report.frames.push(file);
  }
 }
 // Touch-enabled context checks actual tap dispatch, not just desktop click emulation.
 const mobile=await browser.newContext({viewport:{width:390,height:844},hasTouch:true,isMobile:true});
 const phone=await mobile.newPage();
 phone.on('pageerror',e=>errors.push(e.message));
 for(const c of ['a','b']){
  await phone.goto(pathToFileURL(path.join(__dirname,c+'.html')).href+'?frame=phone');
  assert.equal(await phone.locator('[data-mode="Scroll"]').getAttribute('aria-pressed'),'true');
  await phone.locator('.terminal-lines').tap();
  assert.equal(await phone.locator('[data-mode="Scroll"]').getAttribute('aria-pressed'),'true');
  await phone.locator('[data-mode="Type"]').tap();
  assert.equal(await phone.evaluate(()=>document.activeElement.id),'draft');
  report.checks.push(`${c.toUpperCase()}: touch tap on output preserves Scroll mode; explicit Type focuses composer`);
 }
 assert.deepEqual(errors,[]);
 report.checks.push('No JavaScript errors or HTTP(S) requests');
 fs.writeFileSync(path.join(__dirname,'verification.json'),JSON.stringify(report,null,2)+'\n');
 await browser.close();console.log(JSON.stringify(report,null,2));
})().catch(e=>{console.error(e);process.exit(1)});
