const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(mode='public',journey=false){
 const events={},calls=[],saved=new Map(),button={dataset:{},setAttribute(){}},link={},status={};
 const panel={querySelector:s=>s==='a'?link:button};let accepted=false,dirty=false,confirmations=0,rendered=0;
 const context={state:{book:'poems'},
  window:{DOGIDO_BOOTSTRAP:{dataRepository:{url:'https://github.com/yukincom/DokiDoki-Dogido-data',mode},csrf:'csrf'},
   confirm:()=>{confirmations++;return accepted;},dogidoLibraryHasUnsaved:()=>dirty},
  localStorage:{getItem:k=>saved.get(k),setItem:(k,v)=>saved.set(k,v)},
  document:{body:{classList:{contains:()=>journey}},getElementById:()=>({parentElement:{querySelector:()=>null},after(){rendered++;}}),createElement:()=>panel,
   querySelectorAll:s=>s.includes('upload')?[button]:s.includes('status')?[status]:[link],
   addEventListener:(kind,fn)=>events[kind]=fn},
  fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({status:'submitted',url:'https://github.com/yukincom/DokiDoki-Dogido-data/pull/7'})};}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/data-repository.js','utf8'),context);
 return {calls,button,link,status,rendered:()=>rendered,click:()=>events.click({target:{closest:()=>button}}),accept:()=>accepted=true,
  dirty:()=>dirty=true,confirmations:()=>confirmations};
}
test('public upload requires first consent, keeps all-poem endpoint, and links the submitted PR',async()=>{
 const f=fixture();await f.click();assert.equal(f.calls.length,0);
 f.accept();await f.click();assert.equal(f.calls.length,1);
 assert.equal(f.calls[0].url,'/api/research-storage');assert.equal(f.calls[0].options.headers['X-Dogido-Library'],'csrf');
 assert.equal(f.link.href,'https://github.com/yukincom/DokiDoki-Dogido-data/pull/7');
 await f.click();assert.equal(f.confirmations(),2);assert.equal(f.calls.length,2);
});
test('private upload keeps prior consent scope and unsaved edits stop either mode',async()=>{
 const f=fixture('private');await f.click();assert.equal(f.calls.length,1);assert.equal(f.confirmations(),0);
 f.dirty();await f.click();assert.equal(f.calls.length,1);assert.equal(f.button.dataset.result,'error');
});

test('journey page exposes the existing upload only for private storage',()=>{
 assert.equal(fixture('private',true).rendered(),1);
 assert.equal(fixture('public',true).rendered(),0);
});
