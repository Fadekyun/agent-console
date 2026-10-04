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
