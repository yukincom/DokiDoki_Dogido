const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/research-settings.js','utf8');
function fixture(initial=null){
  const detail={innerHTML:''},events=new Map(),requests=[];let chosen='/tmp/archive',failure=false,dirty=false,asked=0;
  const state={book:'catalog',catalogPane:'settings'},scope={state,
    window:{DOGIDO_BOOTSTRAP:{csrf:'token',photos:{researchStorage:initial}},
      dogidoLibraryHasUnsaved:options=>{assert.equal(options.ignoreResearchSettings,true);return dirty;},
      webkit:{messageHandlers:{dogidoResearchDirectory:{postMessage:async kind=>{assert.equal(kind,'chooseResearchDirectory');asked++;return chosen;}}}}},
    document:{body:{classList:{contains:()=>false}},activeElement:null,
      createElement:()=>({addEventListener(){},setAttribute(){}}),
      querySelector:()=>({after(){}}),addEventListener:(kind,fn)=>events.set(kind,fn),dispatchEvent(){}},
    Event:class{constructor(type){this.type=type;}},$:()=>detail,
    esc:value=>String(value).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'),
    closeBookCover(){},renderNavigation(){},renderDetail(){},
    fetch:async(url,options)=>{requests.push({url,options});return {ok:!failure,json:async()=>failure?{detail:'保存先が切断されています'}:{researchStorage:{root:chosen,archiveId:'new-id',state:'ready',poems:2,captures:1}}};}
  };
  vm.createContext(scope);vm.runInContext(source,scope);
  const api=scope.window.DogidoResearchSettings;
  const click=async action=>{events.get('click')({target:{closest:()=>({disabled:false,dataset:{researchAction:action}})}});await new Promise(setImmediate);};
  return {api,detail,requests,click,setChosen:value=>chosen=value,setFailure:value=>failure=value,setDirty:value=>dirty=value,asked:()=>asked};
}
test('unconfigured and pending state render without bogus counts',()=>{
  const f=fixture();f.api.renderPage();assert.doesNotMatch(f.detail.innerHTML,/undefined|null 件/);
  f.api.refresh({root:'/tmp/a<&>',archiveId:'id',state:'pending'});
  assert.match(f.detail.innerHTML,/保全待ち/);assert.match(f.detail.innerHTML,/a&lt;&amp;&gt;/);
});
test('storage failures keep the precise read failure separate from empty archive counts',()=>{
 const f=fixture({root:'/Volumes/ssd/archive',archiveId:'id',state:'error',lastError:'archive_marker_missing <check>'});
 f.api.renderPage();assert.match(f.detail.innerHTML,/接続の確認内容/);assert.match(f.detail.innerHTML,/archive_marker_missing &lt;check&gt;/);
 f.api.refresh({root:'/Volumes/ssd/archive',archiveId:'id',state:'ready'});assert.doesNotMatch(f.detail.innerHTML,/archive_marker_missing/);
});
test('reading source distinguishes verified archive from local fallback',()=>{
 const f=fixture({root:'/tmp/archive',archiveId:'id',state:'ready',displaySource:'archive',readPoems:108});
 f.api.renderPage();assert.match(f.detail.innerHTML,/閲覧元：選択した保存先（108句を照合済み）/);
 f.api.refresh({root:'/tmp/archive',archiveId:'id',state:'ready',displaySource:'unavailable',readError:'archive_snapshot_changed <bad>'});
 assert.match(f.detail.innerHTML,/保存先の読み取りに失敗/);assert.match(f.detail.innerHTML,/archive_snapshot_changed &lt;bad&gt;/);
 assert.doesNotMatch(f.detail.innerHTML,/108句を照合済み/);
});
test('native selection is only a draft until explicit CSRF and CAS save',async()=>{
  const f=fixture({root:'/tmp/old',archiveId:'old-id',state:'ready'});
  await f.click('choose');assert.equal(f.api.hasChanges(),true);assert.equal(f.requests.length,0);
  await f.click('save');assert.equal(f.requests.length,1);assert.equal(f.api.hasChanges(),false);
  assert.equal(f.requests[0].url,'/api/research-storage');
  assert.equal(f.requests[0].options.headers['X-Dogido-Library'],'token');
  assert.deepEqual(JSON.parse(f.requests[0].options.body),{root:'/tmp/archive',expectedArchiveId:'old-id'});
});
test('native cancellation does not change or save a destination',async()=>{
  const f=fixture();f.setChosen(null);await f.click('choose');
  assert.equal(f.api.hasChanges(),false);assert.equal(f.requests.length,0);assert.equal(f.api.busy(),false);
});
test('reconnect uses the existing destination and CAS without asking for a different folder',async()=>{
 const f=fixture({root:'/tmp/current',archiveId:'known-id',state:'error'});
 await f.click('reconnect');assert.equal(f.asked(),0);assert.equal(f.requests.length,1);
 assert.deepEqual(JSON.parse(f.requests[0].options.body),{root:'/tmp/current',expectedArchiveId:'known-id'});
});
test('failed save retains selection and explicit cancellation clears it',async()=>{
  const f=fixture();await f.click('choose');f.setFailure(true);await f.click('save');
  assert.equal(f.api.hasChanges(),true);assert.match(f.detail.innerHTML,/保存先が切断/);
  await f.click('cancel');assert.equal(f.api.hasChanges(),false);assert.equal(f.requests.length,1);
});
test('other unsaved edits prevent picker and save without self-blocking',async()=>{
  const f=fixture();f.setDirty(true);await f.click('choose');assert.equal(f.asked(),0);
  f.setDirty(false);await f.click('choose');f.setDirty(true);await f.click('save');
  assert.equal(f.requests.length,0);assert.equal(f.api.hasChanges(),true);
});
