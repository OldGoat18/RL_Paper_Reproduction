// Run with NODE_PATH pointing to a Playwright installation and a live workbench.
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

(async () => {
  const browser = await chromium.launch({headless: true, args:['--no-sandbox']});
  const page = await browser.newPage({viewport:{width:1440,height:980}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(process.env.HARNESS_URL || 'http://127.0.0.1:8765');
  await page.locator('#project-select option', {hasText:'PPO / CartPole-v1'}).waitFor({state:'attached'});
  await page.locator('#analyze-project').click();
  await page.locator('#analysis-status').filter({hasText:'high confidence'}).waitFor();
  await page.locator('[data-view="executions"]').click();
  await page.locator('#new-run').click();
  await page.locator('#run-form [name=seeds]').fill('1, 2, 3');
  const original = await page.locator('#run-form [name=command]').inputValue();
  // Unique repeat number preserves earlier runs when repeating this test.
  const repeat = process.env.HARNESS_TEST_REPEAT || String(Date.now());
  await page.locator('#run-form [name=command]').fill(original.replaceAll('repeat_{repeat}', `repeat_${repeat}`));
  await page.locator('#run-form [type=submit]').click();
  await page.locator('#run-dialog').waitFor({state:'hidden'});
  await page.locator('#total').filter({hasText:/[3-9]|[1-9][0-9]+/}).waitFor();
  fs.mkdirSync('docs/screenshots', {recursive:true});
  await page.screenshot({path:'docs/screenshots/desktop.png',fullPage:true});
  const image = page.locator('.project-visual img');
  assert(await image.evaluate(el => el.complete && el.naturalWidth > 0));
  for (const width of [390,320]) {
    await page.setViewportSize({width,height:844});
    assert((await page.locator('body').evaluate(el => el.scrollWidth)) <= width, `Overflow at ${width}`);
    await page.locator('#new-run').click();
    assert(await page.locator('#run-form [type=submit]').isVisible());
    await page.locator('#run-dialog .dialog-heading .close').click();
    await page.screenshot({path:`docs/screenshots/mobile-${width}.png`,fullPage:true});
  }
  await page.setViewportSize({width:1440,height:980});
  for (let i=0; i<180; i++) { if (await page.locator('.badge.running,.badge.queued').count() === 0) break; await page.waitForTimeout(1000); }
  await page.locator('#refresh').click();
  assert.equal(await page.locator('.badge.failed').count(), 0);
  await page.locator('.execution-link').first().click();
  assert(await page.locator('[data-open]').isEnabled());
  await page.screenshot({path:'docs/screenshots/execution-detail.png',fullPage:true});
  await page.locator('#detail-dialog .close').click();
  await page.locator('#search').fill('no-such-execution');
  assert(await page.locator('#empty').isVisible());
  await page.locator('#search').fill('');
  await page.screenshot({path:'docs/screenshots/desktop.png',fullPage:true});
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({browser:'passed',repeat,completed:await page.locator('#completed').textContent(),screenshots:'docs/screenshots'},null,2));
  await browser.close();
})().catch(error=>{console.error(error);process.exit(1);});
