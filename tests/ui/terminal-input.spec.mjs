import {test,expect} from '@playwright/test';

async function terminalPage(page){
  const writes=[],controls=[];let currentSocket;
  await page.route('**/api/**',route=>route.fulfill({json:new URL(route.request().url()).pathname==='/api/sessions'?[{id:'typing',tmux_name:'typing',running:true,managed:true}]:{brief:''}}));
  await page.routeWebSocket('**/ws/sessions/**',socket=>{currentSocket=socket;socket.onMessage(data=>{if(Buffer.isBuffer(data))writes.push(data.toString());else controls.push(JSON.parse(data));});socket.send('Input ready\r\n');});
  await page.goto('/terminal?session=typing');await expect(page.locator('#connection')).toHaveText('Connected');await page.locator('.xterm-helper-textarea').focus();return {writes,controls,disconnect:()=>currentSocket.close({code:1012,reason:'Test restart'})};
}

test('direct IME replacement never resends the earlier typing buffer',async({page})=>{
  const {writes}=await terminalPage(page);
  await page.keyboard.type('EARLIER INPUT');await expect.poll(()=>writes.join('')).toBe('EARLIER INPUT');writes.length=0;
  await page.locator('.xterm-helper-textarea').evaluate(textarea=>{
    textarea.value='EARLIER INPUT';textarea.setSelectionRange(13,13);
    textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));
    textarea.value='EARLIER INPUé';
  });
  await expect.poll(()=>writes.join('')).toBe('é');
});

test('one mobile edit after a burst of Process events is transmitted once',async({page})=>{
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(textarea=>{
    textarea.value='';
    for(let i=0;i<250;i++)textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));
    textarea.value='1';textarea.dispatchEvent(new InputEvent('input',{data:'1',inputType:'insertText',bubbles:true,composed:true}));
  });
  await expect.poll(()=>writes.join('')).toBe('1');
});

test('rapid mobile rollover preserves each edit without duplicating suffixes',async({page})=>{
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(textarea=>{
    textarea.value='';
    for(const value of ['a','ab','abb']){textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));textarea.value=value;}
  });
  await expect.poll(()=>writes.join('')).toBe('abb');
});


test('normal typing, real repeats, controls and input-only events remain exact',async({page})=>{
  const {writes}=await terminalPage(page);
  await page.keyboard.type('Aaa 111 repeated letters');await page.keyboard.press('Backspace');await page.keyboard.press('Enter');await page.keyboard.press('Control+C');
  await expect.poll(()=>writes.join('')).toBe('Aaa 111 repeated letters\x7f\r\x03');writes.length=0;
  await page.keyboard.down('x');await page.keyboard.down('x');await page.keyboard.up('x');await expect.poll(()=>writes.join('')).toBe('xx');writes.length=0;
  await page.locator('.xterm-helper-textarea').evaluate(textarea=>textarea.dispatchEvent(new InputEvent('input',{data:'é🙂',inputType:'insertText',bubbles:true,composed:true})));
  await expect.poll(()=>writes.join('')).toBe('é🙂');
});

test('real composition commits once and cancelled composition sends nothing',async({page})=>{
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(async textarea=>{
    textarea.value='';textarea.dispatchEvent(new CompositionEvent('compositionstart',{data:'',bubbles:true}));
    textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));
    textarea.value='你好';textarea.dispatchEvent(new CompositionEvent('compositionupdate',{data:'你好',bubbles:true}));
    await new Promise(resolve=>setTimeout(resolve,0));textarea.dispatchEvent(new CompositionEvent('compositionend',{data:'你好',bubbles:true}));
  });
  await expect.poll(()=>writes.join('')).toBe('你好');writes.length=0;
  await page.locator('.xterm-helper-textarea').evaluate(async textarea=>{
    textarea.dispatchEvent(new CompositionEvent('compositionstart',{data:'',bubbles:true}));
    textarea.dispatchEvent(new CompositionEvent('compositionupdate',{data:'',bubbles:true}));await new Promise(resolve=>setTimeout(resolve,0));textarea.dispatchEvent(new CompositionEvent('compositionend',{data:'',bubbles:true}));await new Promise(resolve=>setTimeout(resolve,10));
  });expect(writes).toEqual([]);
});

test('a pending Process edit is sent before Enter and before a following composition',async({page})=>{
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(async textarea=>{
    textarea.value='';textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));textarea.value='1';
    textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',keyCode:13,bubbles:true,cancelable:true}));
    textarea.value='';textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));textarea.value='2';
    textarea.dispatchEvent(new CompositionEvent('compositionstart',{data:'',bubbles:true}));textarea.value='2好';textarea.dispatchEvent(new CompositionEvent('compositionupdate',{data:'好',bubbles:true}));await new Promise(resolve=>setTimeout(resolve,0));textarea.dispatchEvent(new CompositionEvent('compositionend',{data:'好',bubbles:true}));
  });await expect.poll(()=>writes.join('')).toBe('1\r2好');
});

test('unchanged viewport events do not flood the PTY with resize messages',async({page})=>{
  const {controls}=await terminalPage(page);await expect.poll(()=>controls.filter(x=>x.type==='resize').length).toBeGreaterThan(0);
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));controls.length=0;
  for(let i=0;i<10;i++)await page.evaluate(()=>{window.dispatchEvent(new Event('resize'));return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));});
  expect(controls.filter(x=>x.type==='resize')).toEqual([]);
  await page.setViewportSize({width:720,height:650});await expect.poll(()=>controls.filter(x=>x.type==='resize').length).toBeGreaterThan(0);
});

test('typing during a disconnect is retained for review and never replayed',async({page})=>{
  const {writes,disconnect}=await terminalPage(page);
  await page.route('**/api/sessions?**',route=>route.fulfill({status:503,json:{detail:'Temporarily unavailable'}}));
  disconnect();await expect(page.locator('#connection')).not.toHaveText('Connected');await page.keyboard.type('Lost?');
  await expect(page.locator('#composer')).toHaveValue('Lost?');await expect(page.locator('#input-drawer')).toBeVisible();expect(writes).toEqual([]);
});


test('focus changes flush a pending mobile edit before xterm clears its textarea',async({page})=>{
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(textarea=>{
    textarea.value='';textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));textarea.value='z';document.querySelector('#toggle-composer').focus();
  });await expect.poll(()=>writes.join('')).toBe('z');
});


test('Process keypress and input notifications describe one edit',async({page})=>{
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(textarea=>{
    textarea.value='';textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));
    textarea.dispatchEvent(new KeyboardEvent('keypress',{key:'1',charCode:49,keyCode:49,which:49,bubbles:true,cancelable:true}));
    textarea.value='1';textarea.dispatchEvent(new InputEvent('input',{data:'1',inputType:'insertText',bubbles:true,composed:true}));
  });await expect.poll(()=>writes.join('')).toBe('1');
});

test('successive mobile deletions each send one Backspace',async({page})=>{
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(textarea=>{
    textarea.value='abc';
    for(const value of ['ab','a']){
      textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));
      textarea.value=value;textarea.dispatchEvent(new InputEvent('input',{data:null,inputType:'deleteContentBackward',bubbles:true,composed:true}));
    }
  });await expect.poll(()=>writes.join('')).toBe('\x7f\x7f');
});

test('a Process edit arriving during the deferred composition commit is sent once',async({page})=>{
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(async textarea=>{
    textarea.value='';textarea.dispatchEvent(new CompositionEvent('compositionstart',{data:'',bubbles:true}));
    textarea.value='好';textarea.dispatchEvent(new CompositionEvent('compositionupdate',{data:'好',bubbles:true}));
    await new Promise(resolve=>setTimeout(resolve,0));
    textarea.addEventListener('compositionend',()=>setTimeout(()=>{
      textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));textarea.value='好1';
    },0),{capture:true,once:true});
    textarea.dispatchEvent(new CompositionEvent('compositionend',{data:'好',bubbles:true}));
  });await expect.poll(()=>writes.join('')).toBe('好1');
});

test('Process bursts immediately after composition commits remain bounded',async({page})=>{
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(async textarea=>{
    const subscription=window.__terminal.onData(data=>{
      if(data!=='好')return;
      subscription.dispose();
      for(let i=0;i<250;i++)textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));
      textarea.value='好1';
    });
    textarea.value='';textarea.dispatchEvent(new CompositionEvent('compositionstart',{data:'',bubbles:true}));
    textarea.value='好';textarea.dispatchEvent(new CompositionEvent('compositionupdate',{data:'好',bubbles:true}));
    await new Promise(resolve=>setTimeout(resolve,0));textarea.dispatchEvent(new CompositionEvent('compositionend',{data:'好',bubbles:true}));
  });await expect.poll(()=>writes.join('')).toBe('好1');
});
for (const [selection, start, end] of [['full', 0, 17], ['partial', 4, 10]]) {
  test(`disconnected recovery appends without replacing a hidden ${selection} draft selection`, async ({page}) => {
    const {writes, disconnect} = await terminalPage(page);
    await page.locator('#toggle-composer').click();
    await page.locator('#composer').fill('Recoverable draft');
    await page.locator('#composer').evaluate((composer, range) => composer.setSelectionRange(...range), [start, end]);
    await page.locator('#toggle-composer').click();
    await expect(page.locator('#input-drawer')).toBeHidden();
    await page.route('**/api/sessions?**', route => route.fulfill({status:503,json:{detail:'Temporarily unavailable'}}));
    disconnect();
    await expect(page.locator('#connection')).not.toHaveText('Connected');
    await page.locator('.xterm-helper-textarea').focus();
    await page.keyboard.type('x');
    await expect(page.locator('#composer')).toHaveValue('Recoverable draftx');
    await expect(page.locator('#composer')).toBeFocused();
    await expect(page.locator('#input-drawer')).toBeVisible();
    expect(writes).toEqual([]);
    await page.unroute('**/api/sessions?**');
    await page.reload();
    await expect(page.locator('#connection')).toHaveText('Connected');
    await expect(page.locator('#composer')).toHaveValue('Recoverable draftx');
    expect(writes).toEqual([]);
  });

  test(`explicit manual paste replaces the ${selection} draft selection`, async ({page}) => {
    await page.addInitScript(() => Object.defineProperty(navigator, 'clipboard', {value:undefined, configurable:true}));
    const {writes} = await terminalPage(page);
    await page.locator('#toggle-composer').click();
    await page.locator('#composer').fill('Recoverable draft');
    await page.locator('#composer').evaluate((composer, range) => composer.setSelectionRange(...range), [start, end]);
    await page.locator('#paste-device').click();
    await expect(page.locator('#paste-sheet')).toBeVisible();
    await page.locator('#paste-sheet-text').fill('PASTE');
    await page.locator('#use-manual-paste').click();
    const original = 'Recoverable draft';
    await expect(page.locator('#composer')).toHaveValue(original.slice(0, start) + 'PASTE' + original.slice(end));
    expect(writes).toEqual([]);
  });
}

for (const shape of ['timer gap', 'tight rollover', 'keyup before next input', 'slow keys', 'repeated keys']) {
  test(`input-before-keydown ${shape} transmits each printable edit once`, async ({page}) => {
    const {writes}=await terminalPage(page);
    const expected=shape==='repeated keys'?'jjj':'jk';
    await page.locator('.xterm-helper-textarea').evaluate(async (textarea,shape) => {
      const turn=()=>new Promise(resolve=>setTimeout(resolve,30));
      const down=key=>textarea.dispatchEvent(new KeyboardEvent('keydown',{key,keyCode:229,bubbles:true,cancelable:true}));
      const up=key=>textarea.dispatchEvent(new KeyboardEvent('keyup',{key,keyCode:key.toUpperCase().charCodeAt(0),bubbles:true,cancelable:true}));
      const insert=character=>{
        textarea.value+=character;
        textarea.dispatchEvent(new InputEvent('input',{data:character,inputType:'insertText',bubbles:true,composed:true}));
      };
      textarea.value=''; insert('j'); down('j');
      if(shape==='keyup before next input'||shape==='slow keys')up('j');
      if(shape==='timer gap'||shape==='slow keys'||shape==='repeated keys')await turn();
      const next=shape==='repeated keys'?'j':'k'; insert(next); down(next);
      if(shape==='repeated keys'){await turn();insert('j');down('j');}
      up('j');up(next);await turn();
    },shape);
    await expect.poll(()=>writes.join('')).toBe(expected);
  });
}

test('native keypress remains the owner after a settled Process fallback', async ({page}) => {
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(async textarea => {
    textarea.value='';
    textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));
    await new Promise(resolve=>setTimeout(resolve,30));
    textarea.dispatchEvent(new KeyboardEvent('keypress',{key:'a',keyCode:97,charCode:97,which:97,bubbles:true,cancelable:true}));
    textarea.value='a';
    textarea.dispatchEvent(new InputEvent('input',{data:'a',inputType:'insertText',bubbles:true,composed:true}));
    textarea.dispatchEvent(new KeyboardEvent('keyup',{key:'a',keyCode:65,bubbles:true,cancelable:true}));
  });
  await expect.poll(()=>writes.join('')).toBe('a');
});

test('screen reader input ownership is unchanged for composed Process notifications', async ({page}) => {
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(async textarea => {
    window.__terminal.options.screenReaderMode=true;
    textarea.value='';
    textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));
    await new Promise(resolve=>setTimeout(resolve,30));
    textarea.value='s';
    textarea.dispatchEvent(new InputEvent('input',{data:'s',inputType:'insertText',bubbles:true,composed:true}));
    textarea.dispatchEvent(new KeyboardEvent('keyup',{key:'s',keyCode:83,bubbles:true,cancelable:true}));
    await new Promise(resolve=>setTimeout(resolve,30));
  });
  expect(writes).toEqual([]);
});

test('active composition owns composed insertText notifications', async ({page}) => {
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(async textarea => {
    textarea.value='';
    textarea.dispatchEvent(new CompositionEvent('compositionstart',{data:'',bubbles:true}));
    textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));
    textarea.value='好';
    textarea.dispatchEvent(new CompositionEvent('compositionupdate',{data:'好',bubbles:true}));
    textarea.dispatchEvent(new InputEvent('input',{data:'好',inputType:'insertText',bubbles:true,composed:true}));
    await new Promise(resolve=>setTimeout(resolve,30));
    textarea.dispatchEvent(new CompositionEvent('compositionend',{data:'好',bubbles:true}));
  });
  await expect.poll(()=>writes.join('')).toBe('好');
});

test('pending composition commit owns input before the next Process keydown', async ({page}) => {
  const {writes}=await terminalPage(page);
  await page.locator('.xterm-helper-textarea').evaluate(async textarea => {
    textarea.value='';
    textarea.dispatchEvent(new CompositionEvent('compositionstart',{data:'',bubbles:true}));
    textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));
    textarea.value='好';
    textarea.dispatchEvent(new CompositionEvent('compositionupdate',{data:'好',bubbles:true}));
    await new Promise(resolve=>setTimeout(resolve,30));
    textarea.dispatchEvent(new CompositionEvent('compositionend',{data:'好',bubbles:true}));
    textarea.value='好1';
    textarea.dispatchEvent(new InputEvent('input',{data:'1',inputType:'insertText',bubbles:true,composed:true}));
    textarea.dispatchEvent(new KeyboardEvent('keydown',{key:'Process',keyCode:229,bubbles:true,cancelable:true}));
  });
  await expect.poll(()=>writes.join('')).toBe('好1');
});
