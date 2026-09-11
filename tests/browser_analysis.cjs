const {chromium} = require('playwright');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000},bypassCSP:true});
    const errors=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.goto(process.env.HARNESS_URL || 'http://127.0.0.1:8765');
    await page.waitForFunction(()=>document.querySelector('#project-select').options.length>=3);
    const id=await page.locator('#project-select option').evaluateAll(o=>o.find(p=>p.textContent==='stream_RL').value);
    await page.selectOption('#project-select',id);
    await page.locator('[data-view=project]').click();
    await page.locator('#use-llm').uncheck();
    await page.locator('#analyze').click();
    await page.locator('#analysis-report').getByText('FlowRL / parsed', {exact:false}).waitFor();
    await page.locator('#new-run').click();
    await page.locator('#entry-select').selectOption('external/FlowRL/main.py');
    await page.waitForFunction(()=>document.querySelector('#command-preview').textContent.includes('30 executions'));
    assert.equal((await page.locator('[name=seeds]').inputValue()).split(',').length,30);
    assert.equal(await page.locator('[name=steps]').inputValue(),'5000000');
    assert((await page.locator('#command-preview').textContent()).includes('--num_steps 5000000'));
    assert.equal(await page.locator('#environment-select input').count(),4);
    const picker=page.locator('#environment-select').locator('..');
    await picker.getByRole('button',{name:'Select visible options'}).click();
    await page.waitForFunction(()=>document.querySelector('#command-preview').textContent.includes('120 executions'));
    await picker.getByRole('button',{name:'Clear selection'}).click();
    const rect=await page.locator('#environment-select').boundingBox();
    await page.mouse.move(rect.x+rect.width-3,rect.y+rect.height-3);
    await page.mouse.down();
    await page.mouse.move(rect.x+1,rect.y+1,{steps:15});
    await page.mouse.up();
    assert.equal(await page.locator('#environment-select input:checked').count(),4);
    await page.locator('[name=steps]').fill('123456');
    await page.waitForFunction(()=>document.querySelector('#command-preview').textContent.includes('--num_steps 123456'));
    await page.screenshot({path:'/tmp/harness-analysis-desktop.png',fullPage:true});
    for (const width of [390,320]) {
      await page.setViewportSize({width,height:844});
      assert(await page.locator('#run-dialog').evaluate(e=>e.scrollWidth<=e.clientWidth));
      await page.screenshot({path:'/tmp/harness-analysis-mobile-'+width+'.png',fullPage:true});
    }
    assert.deepEqual(errors,[]);
    console.log('PASS: Web analysis, FlowRL discovery, 30 seeds, total steps, 120-command matrix, drag selection, mobile layout');
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exit(1)});
