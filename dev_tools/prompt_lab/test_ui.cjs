// Frontend regression checks; Node built-ins only, no browser or model calls.
// Usage: node test_ui.cjs [app.js] [optional saved API snapshot]
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const clone=value=>JSON.parse(JSON.stringify(value));
class Element {
  constructor(tag='div',text=''){this.tagName=tag;this.textContent=text;this.value='';this.children=[];this.classList={toggle(){}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
}
const flatten=node=>[node.textContent,...node.children.map(flatten)].join(' ');
function fixture(){
  const candidate={id:'normal-observed-presence',title:'共通プロンプト',components:Object.fromEntries(['base','normal','tension','grounding','dialogue','choice_contract'].map(p=>[p,p])),body_mode:'production',response_format:'speech_json'};
  function run(id,legacy){
    const requests=Array.from({length:15},(_,i)=>({case_id:'case-'+i,repeat:1,label:'モブ'+i,payload:{messages:[]}}));
    return {manifest:{id,created_at:legacy?'2026-10-01':'2026-10-02',status:legacy?'imported':'running',case_count:15,candidate,model:{title:'8080',backend:'http'},...(legacy?{}:{suite:{id:'entity-context',title:'カタログ情報＋個体観測'}})},requests,results:requests.slice(0,legacy?15:14).map(q=>({...q,speech:'保存済み '+q.case_id,metrics:{}})),summary:{count:legacy?15:14,format_ok:0,format_checked:legacy?15:14,watch_cases:0}};
  }
  const runs=[run('import-legacy',true),run('current',false)];
  return {state:{candidates:[candidate],models:[{id:'http-current',title:'8080'}],suites:[{id:'mobs15-entity-context',title:'個体観測'}],notes:[],manager:{running:false},runs:runs.map(({manifest,summary})=>({manifest,summary}))},runs};
}
async function harness(script,data,ids){
  const nodes=new Map();
  const get=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  let failNextRun=false;
  const document={getElementById:get,createElement:tag=>new Element(tag),createTextNode:text=>new Element('#text',text),querySelectorAll:()=>[]};
  const context=vm.createContext({document,URLSearchParams,location:{search:'?'+ids.map(id=>'run='+encodeURIComponent(id)).join('&')},setInterval(){},fetch:async url=>{
    if(url==='/api/state')return {ok:true,json:async()=>clone(data.state)};
    if(url.startsWith('/api/run?')){
      if(failNextRun){failNextRun=false;return {ok:false,status:503,json:async()=>({error:'一時的な読み込み失敗'})};}
      const id=new URL(url,'http://localhost').searchParams.get('id');
      const r=data.runs.find(r=>r.manifest.id===id);
      assert.ok(r,'Unknown run '+id);
      return {ok:true,json:async()=>clone(r)};
    }
    throw Error('Unexpected request: '+url);
  }});
  vm.runInContext(script,context);
  await new Promise(resolve=>setImmediate(resolve));
  const check=()=>{
    assert.equal(get('run-list').children.length,data.runs.length,'Initial list/refresh failed: '+get('message').textContent);
    const table=get('compare').children[0];
    assert.equal(table?.tagName,'table','Comparison missing: '+get('message').textContent);
    assert.equal(table.children.length,16,'All fifteen cases must remain visible');
    assert.equal(table.children[0].children.length,ids.length+1,'Selected comparisons must remain');
    return table;
  };
  check();
  await get('refresh').onclick();
  check();
  return {get,check,failNextRun:()=>{failNextRun=true;}};
}
(async()=>{
  const script=fs.readFileSync(process.argv[2]||path.join(__dirname,'app.js'),'utf8');
  const data=fixture();
  const h=await harness(script,data,['import-legacy','current']);
  assert.ok(flatten(h.check()).includes('過去の取り込み試験'));
  assert.ok(flatten(h.check()).includes('カタログ情報＋個体観測'));
  // A failed comparison fetch must remain retryable on the next refresh.
  const current=data.runs[1];
  current.results.push({...current.requests[14],speech:'再取得後の15件目',metrics:{}});
  current.summary.count=15;
  current.manifest.status='completed';
  h.failNextRun();
  await h.get('refresh').onclick();
  h.check(); // Keep the prior table while the request fails.
  await h.get('refresh').onclick();
  assert.ok(flatten(h.check()).includes('再取得後の15件目'),'Retry did not update the comparison');
  // A full page reload with the same comparison URL must restore both columns.
  await harness(script,data,['import-legacy','current']);
  console.log('PASS: old/new runs, 15 cases, manual refresh, failed-fetch retry, page reload');
  if(process.argv[3]){
    const actual=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
    const latest=actual.runs.filter(r=>r.manifest.suite).sort((a,b)=>b.manifest.created_at.localeCompare(a.manifest.created_at)).slice(0,2).map(r=>r.manifest.id);
    await harness(script,actual,latest);
    const legacy=actual.runs.find(r=>!r.manifest.suite).manifest.id;
    await harness(script,actual,[legacy,latest[0]]);
    console.log('PASS: actual '+actual.runs.length+' saved runs, latest pair and legacy/new comparison');
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
