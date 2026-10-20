const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base=process.env.FIA_TEST_URL||'http://127.0.0.1:5090';
const output=process.env.FIA_SCREENSHOTS||'D:/PWD301/parity-screenshots';
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const errors=[],bad=[];fs.mkdirSync(output,{recursive:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('response',r=>{if(r.status()>=400)bad.push(r.status()+' '+r.url());});
  async function visit(route,label){
   const response=await page.goto(base+route);assert.equal(response.status(),200,route);
   await page.screenshot({path:output+'/'+label+'.png',fullPage:true});
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Overflow '+label);
  }
  for(const [route,label] of [['/','landing'],['/login','login'],['/register','register']]){
   await visit(route,label);await page.setViewportSize({width:390,height:844});await visit(route,label+'-mobile');await page.setViewportSize({width:1440,height:1050});
  }
  async function login(code){await page.goto(base+'/login');await page.locator('[name=mssv]').fill(code);await page.locator('[name=password]').fill('ui-test-password');await Promise.all([page.waitForURL('**/home'),page.locator('button[type=submit]').click()]);}
  await login('QA001');
  const routes=[['/home','dashboard'],['/member','members'],['/event','events'],['/event/1','event-detail'],['/event/new','event-form'],['/tasks','tasks'],['/funds','funds'],['/ai','ai'],['/admin/approve','approvals'],['/admin/roles','roles'],['/profile','profile']];
  for(const [route,label] of routes){await visit(route,label);await page.setViewportSize({width:390,height:844});await visit(route,label+'-mobile');await page.setViewportSize({width:1440,height:1050});}
  await page.goto(base+'/funds');
  for(const tab of ['transactions','dues','collections']){await page.locator('[data-finance-tab='+tab+']').click();assert.equal(await page.locator('[data-finance-panel='+tab+']').isVisible(),true);}
  await page.locator('[data-dialog-open="fund-collection-dialog"]').click();
  assert.ok(await page.locator('#fund-collection-dialog').isVisible());
  assert.match(await page.locator('#fund-collection-dialog form').getAttribute('action'),/\/funds\/collections\/new$/);
  await page.locator('#fund-collection-dialog [data-dialog-close]').first().click();
  await page.goto(base+'/event');
  await page.locator('[data-open-dialog="workspace-links"]').click();assert.ok(await page.locator('#workspace-links').isVisible());await page.keyboard.press('Escape');
  await Promise.all([page.waitForURL('**/login'),page.locator('form.live-logout-form button[type=submit]').click()]);await login('QA002');await page.goto(base+'/funds');
  assert.equal(await page.locator('#fund-collection-dialog').count(),0);
  const pay=page.locator('[data-dialog-open^="fund-payment"]').first();const id=await pay.getAttribute('data-dialog-open');await pay.click();
  assert.ok(await page.locator('#'+id).isVisible());assert.match(await page.locator('#'+id+' form').getAttribute('action'),/\/funds\/collections\/\d+\/payments$/);
  assert.ok(await page.locator('#'+id+' form').evaluate(form=>form.checkValidity()));
  await page.keyboard.press('Escape');await page.goto(base+'/tasks');assert.match(await page.locator('#view').innerText(),/Thiết kế poster/);
  assert.deepEqual(errors,[]);assert.deepEqual(bad,[]);
  console.log('PASS: 14 screens desktop/mobile, finance tabs/dialogs, member payment, live actions, no errors or failed assets');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
