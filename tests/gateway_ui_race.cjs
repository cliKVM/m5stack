// Regression: a delayed screenshot/status for phone A must never enable actions
// against a newly selected phone B. Execute the actual dashboard code in a DOM stub.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function harness() {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      value:'', textContent:'', hidden:false, disabled:false, options:[],
      removeAttribute(name) { delete this[name]; },
      replaceChildren() { this.options=[]; this.value=''; },
      append(value) { this.options.push(value); if (!this.value) this.value=value.value; }
    });
    return elements.get(id);
  }
  const context = vm.createContext({
    document:{getElementById:element,createElement:()=>({})},
    crypto:require('node:crypto'), setInterval:()=>{},
    URL:{createObjectURL:()=> 'blob:test',revokeObjectURL:()=>{}},
    fetch:async()=>{throw Error('Unexpected fetch');},console
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../gateway/clikvm/static/app.js'),'utf8'),context);
  return {context,element,run:code=>vm.runInContext(code,context)};
}

async function delayedStatus() {
  const h=harness(); let release;
  h.context.fetch=()=>new Promise(resolve=>{release=resolve;});
  h.run('token="operator";selectSession("phone-a");globalThis.pending=poll();');
  h.run('selectSession("phone-b");');
  assert.throws(()=>h.run('currentObservation()'));
  release({ok:true,json:async()=>({session_id:'phone-a',device_id:'A',latest_frame:4,simulated:false,intent:null,action:null})});
  await h.context.pending;
  assert.equal(h.run('sid'),'phone-b');
  assert.equal(h.run('latest'),null);
  assert.equal(h.element('screen').hidden,true);
  assert.equal(h.element('run').disabled,true);
}

async function delayedImage() {
  const h=harness(); let releaseBlob, blobStarted;
  const ready=new Promise(resolve=>{blobStarted=resolve;});
  h.context.fetch=async url=>{
    if (url.endsWith('/status')) return {ok:true,json:async()=>({session_id:'phone-a',device_id:'A',latest_frame:4,simulated:false,intent:null,action:null})};
    return {ok:true,blob:()=>new Promise(resolve=>{releaseBlob=resolve;blobStarted();})};
  };
  h.run('token="operator";selectSession("phone-a");globalThis.pending=poll();');
  await ready;
  h.run('selectSession("phone-b");');
  releaseBlob({});
  await h.context.pending;
  assert.equal(h.run('lastFrame'),0);
  assert.equal(h.run('blobUrl'),'');
  assert.equal(h.element('approval').hidden,true);
  assert.equal(h.element('run').disabled,true);
}

async function noSessions() {
  const h=harness();
  h.run('token="operator";selectSession("phone-a");lastFrame=5;latest={session_id:"phone-a",latest_frame:5};');
  h.context.fetch=async()=>({ok:true,json:async()=>({sessions:[]})});
  await h.run('refresh()');
  assert.equal(h.run('sid'),'');
  assert.equal(h.run('lastFrame'),0);
  assert.equal(h.run('latest'),null);
  assert.equal(h.element('run').disabled,true);
  assert.equal(h.element('capture').disabled,true);
}

(async()=>{
  await delayedStatus();
  await delayedImage();
  await noSessions();
  console.log('PASS: delayed status, delayed screenshot and empty-session dashboard isolation');
})().catch(error=>{console.error(error);process.exitCode=1;});
