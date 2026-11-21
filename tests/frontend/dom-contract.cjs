const fs = require('fs');
const assert = require('assert');
const {JSDOM} = require('jsdom');
const {randomUUID,createHash} = require('crypto');
const config = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const {base,token,deviceToken,project} = config;
const checks = [];
const pass = name => checks.push({name,result:'PASS'});
const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));
const auth = credential => ({Authorization:'Bearer '+credential,'Idempotency-Key':randomUUID()});
async function api(path,body,credential=deviceToken) {
  const r = await fetch(base+path,{method:'POST',headers:{...auth(credential),'Content-Type':'application/json'},body:JSON.stringify(body)});
  assert(r.ok, `${path}: ${r.status} ${await r.clone().text()}`); return r.json();
}
async function upload(sid,frame,png) {
  const r = await fetch(base+`/v1/sessions/${sid}/frames`,{method:'POST',headers:{...auth(deviceToken),'Content-Type':'image/png','X-Frame-Id':String(frame)},body:Buffer.from(png,'base64')});
  assert(r.ok,await r.clone().text());return r.json();
}
async function main() {
  const html = await (await fetch(base)).text();
  const js = await (await fetch(base+'/static/app.js')).text();
  const dom = new JSDOM(html,{url:base,runScripts:'outside-only'});
  const w=dom.window,$=id=>w.document.getElementById(id);
  let deferred=null,release=null;
  const requests=[];
  w.fetch=async(path,options)=>{
    requests.push({path,method:options?.method||'GET',headers:options?.headers});
    if(deferred && path===deferred){deferred=null;await new Promise(resolve=>release=resolve);}
    return fetch(new URL(path,base),options);
  };
  w.URL.createObjectURL=URL.createObjectURL;w.URL.revokeObjectURL=URL.revokeObjectURL;
  w.setInterval=()=>0;
  w.eval(js + '\n globalThis.__contractHarness = {poll, selectSession, isPolling: () => polling};');
  const sid=config.sid;
  assert.strictEqual((await fetch(base+`/v1/sessions/${sid}/frames/1`)).status,401);
  assert.strictEqual((await fetch(base+'/v1/sessions',{headers:auth(deviceToken)})).status,403);
  pass('Real HTTP: PNG requires auth, device token cannot list operator sessions');
  $('token').value=token;
  await $('connect').onsubmit({preventDefault(){}});
  assert(!$('screen').hidden);assert($('screen').src.startsWith('blob:'));
  assert(!$('simulation').hidden);assert.strictEqual($('token').value,'');
  assert.strictEqual(w.localStorage.length+w.sessionStorage.length,0);
  pass('Actual dashboard JS login fetches protected PNG as blob, clears input, no storage, simulation label');
  $('goal').value='SIMULATION: harmless HOME and readback';
  await $('intent').onsubmit({preventDefault(){}});
  for(let i=0;i<30&&$('approval').hidden;i++){await sleep(50);await w.__contractHarness.poll();}
  assert(!$('approval').hidden);
  await $('approve').onclick();
  const next=await api(`/v1/sessions/${sid}/actions/next`,{});
  assert.deepStrictEqual(next.action.command,{type:'key',key:'HOME'});
  const action=next.action;
  await api(`/v1/sessions/${sid}/actions/${action.action_id}/result`,{lease_id:action.lease_id,status:'succeeded',detail:'SIMULATED action'});
  await upload(sid,2,config.png);
  for(let i=0;i<30;i++){await w.__contractHarness.poll();if($('status').textContent.includes('Zadanie zakończone'))break;await sleep(50);}
  assert($('status').textContent.includes('Zadanie zakończone'));
  pass('Live dashboard handlers: intent, confirmation, HTTP lease, result, fresh frame and done state');
  // Hold the actual POST so the async handler cannot accidentally appear done
  // merely because loopback HTTP happens to be fast. The capture flag must not
  // exist before dispatch, and must exist after the handler's returned promise.
  deferred=`/v1/sessions/${sid}/capture`;release=null;
  const capturePending=$('capture').onclick();
  assert(capturePending && typeof capturePending.then==='function');
  let captureSettled=false;capturePending.then(()=>captureSettled=true);
  for(let i=0;i<20&&!release;i++)await sleep(10);
  assert(release);
  assert.strictEqual((await api(`/v1/sessions/${sid}/actions/next`,{})).capture_requested,false);
  assert.strictEqual(captureSettled,false);
  release();await capturePending;
  assert((await api(`/v1/sessions/${sid}/actions/next`,{})).capture_requested);
  pass('Delayed dashboard capture POST completes before handler resolves; authenticated flag changes only after dispatch');
  await sleep(50);
  for(let i=0;i<100 && w.__contractHarness.isPolling();i++)await sleep(10);
  assert(!w.__contractHarness.isPolling());
  deferred=`/v1/sessions/${sid}/status`;
  const pending=w.__contractHarness.poll();
  for(let i=0;i<20&&!release;i++)await sleep(10);
  assert(release);
  w.__contractHarness.selectSession('');
  release();await pending;
  assert($('screen').hidden);assert($('run').disabled);assert($('approval').hidden);assert($('uncertain').hidden);
  assert.strictEqual($('device-name').textContent,'Czekam na kontroler');
  pass('Late status response cannot restore image/actions after session selection generation changes');
  const postRequests=requests.filter(r=>r.method==='POST');
  assert(postRequests.length>=3);
  for(const r of postRequests){assert(/^[A-Za-z0-9._:-]{8,128}$/.test(r.headers['Idempotency-Key']));assert(r.headers.Authorization==='Bearer '+token);}
  pass('Actual UI POSTs use scoped bearer and valid unique UUID idempotency headers');
  dom.window.close();

  // A DOM test of the firmware's actual shipped HTML. Its device HTTP endpoint is
  // explicitly simulated; this does not execute ESP-IDF C or verify a physical AP.
  const localHtml=fs.readFileSync(project+'/firmware/main/local.html','utf8');
  const local=new JSDOM(localHtml,{url:'http://192.168.4.1/',runScripts:'outside-only'});
  const lw=local.window, le=id=>lw.document.getElementById(id), calls=[];
  lw.setInterval=()=>0;
  lw.fetch=async(path,options)=>{
    if(!options)return {json:async()=>({provisioning:true,usb:'disconnected',host_started:false})};
    calls.push({path,headers:options.headers,body:JSON.parse(options.body)});
    return {ok:true,json:async()=>({status:'OK'})};
  };
  lw.eval(lw.document.querySelector('script').textContent);
  le('key').value='TESTONLYCODE';
  lw.document.querySelector('[data-key="43"]').click();
  le('address').click();le('click').click();le('voice').click();
  await sleep(10);
  assert(calls.some(r=>r.path==='/api/km'&&r.body.usage===43&&r.body.modifier===0));
  assert(calls.some(r=>r.path==='/api/km'&&r.body.usage===15&&r.body.modifier===1));
  assert(calls.some(r=>r.path==='/api/km'&&r.body.type==='click'));
  assert(calls.some(r=>r.path==='/api/request'&&r.body.type==='voice'));
  assert(calls.every(r=>r.headers['X-Control-Key']==='TESTONLYCODE'&&r.headers['Content-Type']==='application/json'));
  pass('Actual embedded HTML handlers send correct AOA/control request shapes to simulated device API');
  local.window.close();
  const report={schema:'clikvm.frontend-contract-check/v1',scope:'Actual JS/HTML in jsdom plus live gateway HTTP; embedded C API simulated',rendered_browser:'NOT_RUN: DOM test has no raster renderer; see integration-review.md for the separate blocked Chromium attempt',real_hardware_tested:false,node_version:process.versions.node,source_sha256:{'gateway/clikvm/static/app.js':createHash('sha256').update(js).digest('hex'),'firmware/main/local.html':createHash('sha256').update(localHtml).digest('hex')},checks};
  fs.writeFileSync(project+'/docs/analysis/browser-contract-check.json',JSON.stringify(report,null,2)+'\n');
  console.log(JSON.stringify(report,null,2));
}
main().catch(e=>{console.error(e);process.exitCode=1;});
