import {test,expect} from '@playwright/test';

async function open(page){
  const writes=[];
  await page.route('**/api/**',route=>{
    const path=new URL(route.request().url()).pathname;
    return route.fulfill({json:path==='/api/sessions'?[{id:'native',tmux_name:'native',running:true,managed:true}]:{brief:''}});
  });
  await page.routeWebSocket('**/ws/sessions/**',socket=>{
    socket.onMessage(data=>{if(Buffer.isBuffer(data))writes.push(data.toString());});
    socket.send('COPYTHIS text\r\n\x1b[?1000h\x1b[?1006h\x1b[?2004h');
  });
  await page.goto('/terminal?session=native');
  await expect(page.locator('#connection')).toHaveText('Connected');
  await expect.poll(()=>page.evaluate(()=>window.__terminal.modes.mouseTrackingMode)).toBe('vt200');
  return writes;
}
async function mode(page,name){
  await page.getByRole('button',{name:'Terminal options',exact:true}).click();
  await page.locator(`[data-mode="${name}"]`).click();
}
async function drag(page,reverse=false){
  const box=await page.locator('.xterm-screen').boundingBox();
  const {cols,rows}=await page.evaluate(()=>({cols:window.__terminal.cols,rows:window.__terminal.rows}));
  const start=box.x+1,end=box.x+box.width/cols*8,y=box.y+box.height/rows/2;
  await page.mouse.move(reverse?end:start,y);await page.mouse.down();
  await page.mouse.move(reverse?start:end,y,{steps:10});await page.mouse.up();
  await expect.poll(()=>page.evaluate(()=>window.__terminal.getSelection())).toBe('COPYTHIS');
}

test('Select drag works with TUI mouse tracking without sending mouse input',async({page})=>{
  const writes=await open(page);await mode(page,'select');await drag(page);await drag(page,true);
  expect(writes).toEqual([]);
});

test('real keyboard Copy and terminal paste retain bracketed input and Ctrl-C interrupt',async({page},info)=>{
  test.skip(info.project.name!=='desktop','Native keyboard shortcuts use a desktop keyboard.');
  const writes=await open(page);await mode(page,'select');await drag(page);
  await page.keyboard.press('Control+C');await mode(page,'type');
  await page.keyboard.press('Control+V');
  await expect.poll(()=>writes).toEqual(['\x1b[200~COPYTHIS\x1b[201~']);
  await expect(page.locator('#composer')).toHaveValue('');
  // Pasting clears xterm selection. Ctrl-C now keeps its interrupt meaning.
  await page.keyboard.press('Control+C');await expect.poll(()=>writes.at(-1)).toBe('\x03');
});

test('terminal-style Ctrl-Shift-C copies a genuine drag selection',async({page},info)=>{
  test.skip(info.project.name!=='desktop','Native keyboard shortcuts use a desktop keyboard.');
  const writes=await open(page);await mode(page,'select');await drag(page);
  await page.keyboard.press('Control+Shift+C');await expect(page.locator('#connection')).toHaveText('Selection copied');
  await mode(page,'type');await page.keyboard.press('Control+Shift+V');
  await expect.poll(()=>writes).toEqual(['\x1b[200~COPYTHIS\x1b[201~']);
});

test('touch Select drag is not cancelled by browser panning',async({page},info)=>{
  test.skip(info.project.name==='desktop','Touch gestures use the mobile layouts.');
  const writes=await open(page);await mode(page,'select');
  const box=await page.locator('.xterm-screen').boundingBox();
  const {cols,rows}=await page.evaluate(()=>({cols:window.__terminal.cols,rows:window.__terminal.rows}));
  const x=box.x+1,y=box.y+box.height/rows/2,end=box.x+box.width/cols*8;
  await page.evaluate(()=>{window.__pointerCancelled=false;document.querySelector('#terminal').addEventListener('pointercancel',()=>window.__pointerCancelled=true);});
  const cdp=await page.context().newCDPSession(page);
  try{
    await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y,id:1}]});
    for(let step=1;step<=8;step++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x:x+(end-x)*step/8,y,id:1}]});
    await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  }finally{await cdp.detach();}
  await expect.poll(()=>page.evaluate(()=>window.__terminal.getSelection())).toBe('COPYTHIS');
  expect(await page.evaluate(()=>window.__pointerCancelled)).toBe(false);expect(writes).toEqual([]);
  await page.evaluate(()=>{window.__copyAttempts=0;document.execCommand=()=>{window.__copyAttempts++;return false;};Object.defineProperty(navigator,'clipboard',{configurable:true,value:undefined});});
  await page.locator('#copy-selection').tap();
  await expect(page.locator('#copy-sheet-text')).toHaveValue('COPYTHIS');
  await expect(page.locator('#copy-sheet-text')).toBeFocused();
  await page.locator('[data-close="copy-sheet"]').tap();
  expect(await page.evaluate(()=>window.__copyAttempts)).toBe(1);
  await page.locator('#paste-clipboard').tap();await expect(page.locator('#paste-sheet-text')).toBeFocused();
});

test('plain shells retain native double-click word selection in Select mode',async({page},info)=>{
  test.skip(info.project.name!=='desktop','Mouse word selection uses a desktop pointer.');
  const writes=await open(page);await page.evaluate(()=>new Promise(resolve=>window.__terminal.write('\x1b[?1000l\x1b[?1006l',resolve)));
  await mode(page,'select');const box=await page.locator('.xterm-screen').boundingBox();
  const rows=await page.evaluate(()=>window.__terminal.rows);
  await page.mouse.dblclick(box.x+8,box.y+box.height/rows/2);
  await expect.poll(()=>page.evaluate(()=>window.__terminal.getSelection())).toBe('COPYTHIS');expect(writes).toEqual([]);
});
