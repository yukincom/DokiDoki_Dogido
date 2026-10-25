const test=require('node:test'),assert=require('node:assert/strict');
const {selectLayer,createReader}=require('./j-lens-viewer.js');
test('kana and symbols copy locally while kanji-only words wait for the shared dictionary',()=>{
 const {copyOriginal}=require('./j-lens-viewer.js');
 for(const piece of ['カタカナ','おはよう','▁▁','<turn|>'])assert.equal(copyOriginal(piece),true);
 for(const piece of ['貴重','谢谢','欢迎','précieux','treasure'])assert.equal(copyOriginal(piece),false);
});
test('one saved readout is shared by phase panes and only a missing cache triggers a guarded POST',async()=>{
 const calls=[];let release;const ready=new Promise(resolve=>release=resolve);
 const read=createReader(async(url,options)=>{calls.push({url,options});if(calls.length===1)return {status:404};await ready;return {ok:true,json:async()=>({schema:'data'})};},'csrf-test');
 const first=read('/capture/1'),second=read('/capture/1');assert.equal(first,second);
 release();assert.deepEqual(await first,{schema:'data'});assert.equal(calls.length,2);
 assert.equal(calls[1].options.method,'POST');assert.equal(calls[1].options.headers['X-Dogido-Library'],'csrf-test');
 assert.equal(read('/capture/1'),first);
});
test('missing configuration does not trigger work and failed requests remain retryable',async()=>{
 let count=0;const read=createReader(async()=>{count++;return {status:503,ok:false,json:async()=>({detail:'未設定'})};},'token');
 await assert.rejects(read('x'),/未設定/);await assert.rejects(read('x'),/未設定/);assert.equal(count,2);
});
test('native final head is distinct and exact token pieces survive without semantic filtering',()=>{
 const candidates=Array.from({length:16},(_,id)=>({id,piece:id?'▁<x>':'\n',score:16-id}));
 const layer={layer:29,method:'native_final_head',candidates};
 const data={schema:'dogido.j_lens.v1',status:'exploratory_transfer',positions:[{phase:'after',position:99,layers:Array(29).fill(null).concat([layer])}]};
 assert.equal(selectLayer(data,'after',29).row,layer);assert.equal(layer.candidates[0].piece,'\n');
 assert.throws(()=>selectLayer(data,'during',29));assert.throws(()=>selectLayer(data,'after',28));
 assert.throws(()=>selectLayer({...data,status:'validated_meaning'},'after',29));
});
