const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture(records){
 const elements=new Map(),events={};
 const element=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',querySelector:()=>null});return elements.get(id);};
 const context={window:{DOGIDO_BOOTSTRAP:{journeys:records,player:{journeys:{}}}},
  $:element,M:{poems:[]},esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;'),
  document:{body:{classList:{contains:name=>name==='library-journeys'}},querySelectorAll:()=>[],
   addEventListener:(name,fn)=>events[name]=fn,dispatchEvent(){}},CustomEvent:class{constructor(type){this.type=type;}}};
 vm.runInNewContext(fs.readFileSync(__dirname+'/journeys.js','utf8'),context);
 return {html:()=>element('journey-detail').innerHTML,list:()=>element('journey-list').innerHTML,
  search:value=>events.input({target:{id:'journey-search',value,dataset:{}}})};
}
function record(extra={}){return {id:'journey_test',date:'2026-10-26',started_at:'2026-10-26T01:00:00Z',ended_at:'2026-10-26T01:05:00Z',player:'プレイヤー',synopses:[],poems:[],unfinished:false,...extra};}
test('short adventure reads and searches raw text with speaker and event distinctions',()=>{
 const f=fixture([record({raw_tail:[{role:'user',text:'橋を見に行こう。'},{role:'assistant',text:'一緒に見よか。'},
  {role:'event',reaction:'silent',text:'沈黙の目印。<script>危険</script>'}]})]);
 assert.match(f.list(),/橋を見に行こう。/);assert.match(f.html(),/未要約のやりとり/);
 assert.match(f.html(),/<strong>ドギド<\/strong><span>一緒に見よか。/);
 assert.match(f.html(),/<strong>出来事<\/strong><span>沈黙の目印。&lt;script&gt;/);
 assert.doesNotMatch(f.html(),/<script>|あらすじは残っていません/);
 f.search('沈黙の目印');assert.match(f.html(),/橋を見に行こう。/);
 f.search('存在しない語');assert.equal(f.list(),'');
});
test('legacy summary-only record keeps all summaries in order',()=>{
 const f=fixture([record({synopses:[{text:'最初の冒険。'},{text:'後の冒険。'}]})]);
 assert.ok(f.html().indexOf('最初の冒険。')<f.html().indexOf('後の冒険。'));
 assert.doesNotMatch(f.html(),/未要約のやりとり/);
});
