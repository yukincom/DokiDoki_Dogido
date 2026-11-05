// 見込み: 取得した読み辞書の編集可否を既存画面へ反映し、子機から変更要求を送らない。
// 合格: 一覧を表示したまま編集を隠し、操作イベントでも保存しない。範囲: 実JSと模擬DOM。実ブラウザー描画・HTTP・音声は別確認。
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const readingModel=require('./reading-model.js');
const record={id:'host-1',surface:'草地',reading:'くさち',wrong_reading:'そうち',source:'biome:meadow'};

function element(tag='div'){
  return {tagName:tag.toUpperCase(),value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,dataset:{},children:[],
    classList:{contains:()=>false},setAttribute(){},addEventListener(){},focus(){},
    append(...children){this.children.push(...children);},
    replaceChildren(...children){this.children=children;if(this.tagName==='SELECT')this.value=children[0]?.value||'';},
    querySelector(){return element();},querySelectorAll(){return [];}};
}

async function catalogFixture(writable){
  const html=fs.readFileSync(__dirname+'/../../dogido-rust/src/server/catalog.html','utf8');
  const nodes=new Map([...html.matchAll(/<([a-z]+)[^>]*\bid="([^"]+)"[^>]*>/g)].map(match=>[match[2],element(match[1])]));
  const requests=[],snapshot={enabled:true,entries:[{id:'biome:meadow',kind:'biome',surface:'草地',reading:'そうち',note:'山の草原'}],corrections:[record]};
  if(writable!==undefined)snapshot.readings_writable=writable;
  const scope={document:{getElementById:id=>nodes.get(id),createElement:element,
    querySelectorAll:()=>[...nodes.values()].filter(el=>['INPUT','SELECT','BUTTON'].includes(el.tagName))},
    fetch:async(path,options)=>{requests.push({path,...options});return {ok:true,json:async()=>options.method==='GET'?snapshot:{correction:{...record,id:'saved-2',reading:'くさば'}}};}};
  vm.createContext(scope);vm.runInContext(html.match(/<script>([\s\S]*)<\/script>/)[1],scope);
  await new Promise(resolve=>setImmediate(resolve));
  return {nodes,requests};
}

// 見込み: 共有または編集可否が不明な取得結果は、検索・一覧を保って編集を閉じる。
// 合格: 読みと説明を表示し、編集欄・ボタンがなく、疑似submitでもGET以外を送らない。範囲: catalog.html全JSと模擬DOM。
test('catalog keeps shared readings searchable while local edit events send no write',async()=>{
  for(const writable of [false,undefined]){
    const f=await catalogFixture(writable),get=id=>f.nodes.get(id);
    assert.equal(get('detail').textContent,'読み：くさち\n山の草原');
    assert.equal(get('reading-editor').hidden,true);assert.equal(get('edit-intro').hidden,true);
    assert.equal(get('choose').hidden,true);assert.equal(get('save').disabled,true);
    const article=get('saved').children[0];
    assert.equal(article.children.length,1);assert.equal(article.children[0].children[1].textContent,'くさち');
    get('choose').onclick();get('surface').value='草地';get('reading').value='そうち';
    await get('edit').onsubmit({preventDefault(){}});
    get('search').value='くさち';get('search').oninput();
    assert.equal(get('entry').children.length,1);
    assert.deepEqual(f.requests.map(r=>r.method),['GET']);
  }
});

// 見込み: 親機の編集可能な取得結果では既存の保存入口を引き続き使える。
// 合格: 編集・取消ボタンが現れ、現在ID付きPUTを送り、応答の読みが一覧へ反映する。範囲: catalog.html全JS、応答は固定。
test('catalog writable mode preserves the existing edit and save route',async()=>{
  const f=await catalogFixture(true),get=id=>f.nodes.get(id);
  assert.equal(get('reading-editor').hidden,false);assert.equal(get('choose').hidden,false);
  assert.equal(get('saved').children[0].children.length,3);
  get('choose').onclick();get('reading').value='くさば';
  await get('edit').onsubmit({preventDefault(){}});
  assert.equal(f.requests[1].path,'/api/v1/catalog/readings');assert.equal(f.requests[1].method,'PUT');
  assert.equal(JSON.parse(f.requests[1].body).expected_id,'host-1');
  assert.equal(get('saved').children[0].children[0].children[1].textContent,'くさば');
});

function bookshelfFixture(writable,flags={}){
  const nodes=new Map(['detail','categories','index-foot'].map(id=>[id,element()])),events={},requests=[];
  const document={body:element('body'),createElement:element,
    addEventListener:(name,callback)=>events[name]=callback,dispatchEvent(){},
    querySelector:selector=>selector==='.readings-page'?element():null,querySelectorAll:()=>[]};
  const selected=[],scope={window:{DOGIDO_BOOTSTRAP:{corrections:[record],readings_writable:writable,csrf:'test',...flags},DogidoReadingModel:readingModel},
    document,state:{book:'catalog',catalogPane:'readings'},$:id=>nodes.get(id),esc:String,indexPanel:element(),
    renderNavigation(){},renderDetail(){},renderList(){},current:()=>null,canSelectPoems:()=>true,
    selectedPoems:{ids:()=>[],set:(id,value)=>selected.push([id,value])},
    fetch:async(...args)=>{requests.push(args);throw Error('unexpected write');},Event:class{}};
  vm.createContext(scope);vm.runInContext(fs.readFileSync(__dirname+'/bookshelf-controls.js','utf8'),scope);
  scope.window.DogidoReadings.renderPage();
  return {scope,nodes,events,requests,selected};
}

// 見込み: 子機の本棚は読みを文字で表示し、句の選択は従来どおり扱う。
// 合格: 読みの入力・追加・削除が消え、古いイベントと保存でも変更せず、句選択は通る。範囲: controls全JSと模擬DOM。
test('bookshelf shared readings expose no editing controls and do not disable poem selection',async()=>{
  const f=bookshelfFixture(false),ui=f.scope.window.DogidoReadings;
  assert.match(f.nodes.get('detail').innerHTML,/<td>くさち<\/td><td>草地<\/td>/);
  assert.doesNotMatch(f.nodes.get('detail').innerHTML,/<input|data-reading-add|data-remove-selected-readings/);
  f.events.input({target:{dataset:{readingKey:'saved-host-1'},value:'そうち'}});
  f.events.submit({target:{matches:()=>true},preventDefault(){}});
  await ui.save();assert.equal(ui.hasChanges(),false);assert.equal(ui.hasInput(),false);assert.deepEqual(f.requests,[]);
  f.scope.state.book='poems';f.events.change({target:{dataset:{poemSelect:'poem-1'},checked:true}});
  assert.deepEqual(f.selected,[['poem-1',true]]);
  const parent=bookshelfFixture(true).nodes.get('detail').innerHTML;
  assert.match(parent,/data-reading-add/);assert.match(parent,/data-reading-key/);assert.match(parent,/data-remove-selected-readings/);
});

// 見込み: 子用権限があるときだけ読み辞書と削除導線を隠し、古い操作イベントも書込みへ進めない。
// 合格: 子に辞書・削除メニューがなく、句選択・写真削除を呼んでも要求ゼロ。親用は従来どおり。範囲: 実controlsと模擬DOM。
test('family child hides dictionary and trash and ignores stale delete events',async()=>{
  const f=bookshelfFixture(false,{child_mode:true,can_delete:false,show_readings:false});
  assert.equal(f.nodes.get('detail').innerHTML,'');
  assert.equal(f.nodes.get('categories').children.length,0);
  f.scope.state.book='poems';f.events.change({target:{dataset:{poemSelect:'poem-1'},checked:true}});
  f.scope.window.DogidoBookshelfDeletePhoto('photo-1','写真');
  await f.scope.window.DogidoReadings.save();
  assert.deepEqual(f.selected,[]);assert.deepEqual(f.requests,[]);
  const parent=bookshelfFixture(true);
  assert.ok(parent.nodes.get('categories').children.length>0);
});

// 見込み: 子の入力エラーは固定案内で扱い、本人用の文面と保存結果の不確実性を保つ。
// 合格: 三行の直し方と結果未確認がドギドの口調になり、本人用は元文面。範囲: 実エラー表示関数、モデル呼出しなし。
test('child error wording explains correction without claiming a successful save',()=>{
  const app=fs.readFileSync(__dirname+'/app.js','utf8');
  const scope={window:{DOGIDO_BOOTSTRAP:{child_mode:true,parent_view:false}}};vm.createContext(scope);
  vm.runInContext(app.slice(0,app.indexOf('/* 本体の本棚データ')),scope);
  const format=scope.window.dogidoLibraryError;
  assert.match(format(Error('句は改行で三行に分けて入力してください。')),/三行に分けてな/);
  const unknown=format(Error('句の保存結果を確認できませんでした。入力は残っています。'));
  assert.match(unknown,/まだ確認できへん/);assert.doesNotMatch(unknown,/保存できたで|保存しました/);
  scope.window.DOGIDO_BOOTSTRAP.parent_view=true;assert.equal(format(Error('元の案内')),'元の案内');
});
