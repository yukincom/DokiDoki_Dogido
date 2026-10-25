const test=require('node:test'),assert=require('node:assert/strict');
const viewer=require('./internal-capture-viewer.js');
test('raw values retain their decimal precision and display coordinates are bounded',()=>{
 const fields={position:{value:'300',max:'300'},layer:{value:'30',max:'30'},offset:{value:'2816',max:'2816'}};
 assert.deepEqual(viewer.coordinates(fields),{position:299,layer:29,offset:2815});
 for(const value of ['0','301','1.5','NaN','']){
  assert.throws(()=>viewer.coordinates({...fields,position:{value,max:'300'}}));
 }
 assert.equal(viewer.formatValues({offset:64,values:[0.12345679104328156,-0]}),'65\t0.12345679104328156\n66\t-0');
 assert.throws(()=>viewer.formatValues({offset:0,values:Array(65).fill(0)}));
});
test('a late response never overwrites the newly selected position or a detached page',async()=>{
 const pending=[],fetchValues=(url,options)=>new Promise(resolve=>pending.push({url,options,resolve}));
 const load=viewer.createLoader(fetchValues),fields={position:{value:'1',max:'300'},layer:{value:'1',max:'30'},offset:{value:'1',max:'2816'}};
 const status={textContent:''},output={textContent:''};
 const view={isConnected:true,dataset:{captureBase:'/api/poems/p/internal-capture/1'},querySelector(selector){
  if(selector==='.capture-value-status')return status;
  if(selector==='.capture-values')return output;
  return fields[selector.match(/name="(.+)"/)[1]];
 },querySelectorAll:()=>[]};
 const response=position=>({ok:true,json:async()=>({position,layer:0,offset:0,shape:[300,30,2816],predicted_token_id:7,input_position:position+4,values:[position]})});
 const first=load(view);fields.position.value='300';const last=load(view);
 assert(pending[0].options.signal.aborted);assert.match(pending[1].url,/position=299&layer=0&offset=0$/);
 pending[1].resolve(response(299));await last;assert.equal(output.textContent,'1\t299');
 pending[0].resolve(response(0));await first;assert.equal(output.textContent,'1\t299');
 const detached=load(view);view.isConnected=false;pending[2].resolve(response(299));await detached;
 assert.equal(output.textContent,'');
});
