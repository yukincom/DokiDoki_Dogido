const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const M=require('./model.js');
const poems=[
 {id:'root:one',date:'2026-10-17',lines:['あさのやま','とりのこえする','もりのみち'],preface:'鳥の声から、なんか浮かんできたわ。'},
 {id:'session:a:one',date:'2026-10-16',lines:['かわべりで','さかなをまって','ひがくれる'],preface:null},
 {id:'root:three',date:'2026-10-15',lines:['よるのもり','きのかげゆれる','かぜのおと'],preface:'<見どころ>は原文のまま。'}
];
test('selection keeps exact source-qualified IDs, rejects unknown IDs and prunes hidden entries',()=>{
 const before=JSON.stringify(poems),selection=M.createPoemSelection(poems);
 assert(selection.set('root:one',true));assert(selection.set('session:a:one',true));assert(!selection.set('unknown',true));
 selection.set('root:one',true);assert.equal(selection.ids().length,2);
 const ids=selection.ids();ids.pop();assert.equal(selection.ids().length,2);
 selection.retain([poems[1]]);assert.deepEqual(selection.ids(),['session:a:one']);
 selection.set('session:a:one',false);assert.deepEqual(selection.ids(),[]);
 assert.equal(JSON.stringify(poems),before);
});
test('batch trash sends only checked IDs once and waits for each completion',async()=>{
 const selection=M.createPoemSelection(poems);selection.set('root:one',true);selection.set('root:three',true);
 const calls=[];let active=0;
 const result=await selection.trash(async id=>{assert.equal(active++,0);calls.push(id);await Promise.resolve();active--;});
 assert.deepEqual(calls,['root:one','root:three']);assert.deepEqual(result.removed,calls);assert.deepEqual(result.remaining,[]);assert.equal(result.error,null);
 assert.deepEqual(selection.ids(),[]);
});
test('partial failure stops the batch, keeps remaining checks and retries no completed IDs',async()=>{
 const selection=M.createPoemSelection(poems);for(const p of poems)selection.set(p.id,true);
 const calls=[],failure=Error('一時エラー');
 const first=await selection.trash(async id=>{calls.push(id);if(id==='session:a:one')throw failure;});
 assert.deepEqual(calls,['root:one','session:a:one']);assert.deepEqual(first.removed,['root:one']);assert.equal(first.error,failure);
 assert.deepEqual(first.remaining,['session:a:one','root:three']);
 const retry=[];await selection.trash(async id=>retry.push(id));
 assert.deepEqual(retry,['session:a:one','root:three']);assert.deepEqual(selection.ids(),[]);
});
const app=fs.readFileSync(__dirname+'/app.js','utf8');
const escape=app.slice(app.indexOf('const esc='),app.indexOf('const glyphs='));
test('poem page places exact preface above the poem, has an icon-only bookmark and no redundant labels',()=>{
 const detail={innerHTML:''},state={favorites:new Set()},scope={state,window:{DogidoPoemRecord:require('./poem-record.js')},playerNotes:{get:()=>'<自分の句>'},poemTags:()=>'<span class="test-tags">バイオーム・時間帯</span>',scene:()=>'',e:poems[2],$:()=>detail};
 vm.createContext(scope);
 const render=app.slice(app.indexOf('function renderPoem('),app.indexOf('function renderNavigation('));
 vm.runInContext(escape+render+'renderPoem(e);',scope);
 const html=detail.innerHTML;
 assert(html.indexOf('&lt;見どころ&gt;は原文のまま。')<html.indexOf('class="poem-scene"'));
 assert.match(html,/class="folio-head poem-page-meta"><span class="test-tags">バイオーム・時間帯<\/span><time datetime="2026-10-15">2026\.10\.15<\/time><button class="favorite bookmark-button"/);assert.match(html,/&lt;自分の句&gt;/);
 assert(html.indexOf('id="favorite"')<html.indexOf('class="entry-heading"'));
 assert.doesNotMatch(html,/保存された句|ドギド 作|元句|この句のこと|ドギドが句を読む前|player-note-help|page-footer|句集 \/|copy-text|delete-current-poem/);
 assert.match(html,/<button[^>]*aria-label="しおりを挟む"[^>]*aria-pressed="false"><svg/);
 state.favorites.add(poems[2].id);vm.runInContext('renderPoem(e);',scope);
 assert.match(detail.innerHTML,/aria-label="しおりを外す"[^>]*aria-pressed="true"><svg/);
 scope.e=poems[1];vm.runInContext('renderPoem(e);',scope);assert.match(detail.innerHTML,/<p class="poem-preface"><\/p>/);
});
test('poem list checks and reading buttons are separate and selection survives opening another poem',()=>{
 const elements={'result-count':{},'entry-list':{}},selection=M.createPoemSelection(poems);selection.set('root:one',true);
 const state={book:'poems',poemSection:'all',selected:'session:a:one'},scope={state,selectedPoems:selection,entries:()=>poems,poemTags:()=>'<span class="test-tags">バイオーム・時間帯</span>',renderDetail(){},$:id=>elements[id]};vm.createContext(scope);
 const render=app.slice(app.indexOf('function canSelectPoems('),app.indexOf('function folio('));vm.runInContext(escape+render+'renderList();',scope);
 const html=elements['entry-list'].innerHTML;
 assert.match(html,/data-poem-select="root:one"[^>]*checked><button class="entry"/);
 assert.match(html,/data-entry="session:a:one" aria-pressed="true"/);
 assert.match(html,/<span class="poem-list-body"><span class="poem-list-thumbnail" data-poem-thumbnail="root:one" aria-hidden="true"><\/span><strong class="poem-lines">/);
 assert.match(html,/<span class="poem-list-meta"><span class="test-tags">バイオーム・時間帯<\/span><time datetime="2026-10-17">2026\.10\.17<\/time>/);
 assert.doesNotMatch(html,/>10 \/ 17</);
 assert.equal((html.match(/type="checkbox"/g)||[]).length,3);assert.doesNotMatch(html,/元句|直し句|ドギド 作|プレイヤー/);
 assert.deepEqual(selection.ids(),['root:one']);
 state.poemSection='favorites';vm.runInContext('renderList();',scope);
 assert.doesNotMatch(elements['entry-list'].innerHTML,/data-poem-select|type="checkbox"/);assert.deepEqual(selection.ids(),[]);
 state.poemSection='all';vm.runInContext('renderList();',scope);assert.doesNotMatch(elements['entry-list'].innerHTML,/ checked/);
});
test('delete and restore icons stay inside the all-poems index and hide on bookmarks',()=>{
 const source=fs.readFileSync(__dirname+'/bookshelf-controls.js','utf8'),placed=[];
 const meta={},state={book:'poems'},poemActions={buttons:[],append(...buttons){this.buttons.push(...buttons);}},indexHead={append(element){placed.push(element);}};
 const scope={state,poemActions,indexPanel:{querySelector:selector=>{assert(['.index-head','.result-meta'].includes(selector));return selector==='.index-head'?indexHead:meta;}},deletePoemsButton:{},restoreButton:{},document:{body:{classList:{contains:()=>false}}},canSelectPoems:()=>true,updatePoemSelection(){},$:()=>{throw Error('must not place actions between navigation categories');}};
 vm.createContext(scope);vm.runInContext(source.slice(source.indexOf('  function placeMenuActions('),source.indexOf('  const originalNavigation='))+'placeMenuActions();',scope);
 assert(placed.includes(poemActions));assert.equal(poemActions.hidden,false);assert.equal(poemActions.buttons.length,3);assert.equal(poemActions.buttons[2],meta);
 scope.canSelectPoems=()=>false;vm.runInContext('placeMenuActions();',scope);assert.equal(poemActions.hidden,true);
 state.book='catalog';scope.$=()=>({append:button=>assert.equal(button,scope.restoreButton)});vm.runInContext('placeMenuActions();',scope);assert.equal(poemActions.hidden,true);
 assert.match(source,/restoreButton\.setAttribute\('aria-label','復元'\)/);assert.match(source,/restoreButton\.innerHTML='<svg/);assert.doesNotMatch(source,/restoreButton\.textContent=/);
 assert.match(source,/data-icon="restore-return"/);assert.doesNotMatch(source,/M5 4v5A10/);
});
test('bookmark view refuses both delete confirmation and execution before touching data',async()=>{
 const source=fs.readFileSync(__dirname+'/bookshelf-controls.js','utf8'),scope={busy:false,canSelectPoems:()=>false,canReload(){throw Error('must stop before save or data operations');}};
 vm.createContext(scope);const start=source.indexOf('  function confirmSelectedPoems('),end=source.indexOf('  function showReadings(');
 assert(end>start);vm.runInContext(source.slice(start,end)+'confirmSelectedPoems();result=deleteSelectedPoems({});',scope);await scope.result;
});
test('unsaved-delete guard displays an HTML dialog without native alert or data changes',()=>{
 const source=fs.readFileSync(__dirname+'/bookshelf-controls.js','utf8'),guard=source.slice(source.indexOf('  function canReload(){'),source.indexOf('  async function request('));
 let shown=0;const dialog={dataset:{},innerHTML:'',open:false,showModal(){shown++;this.open=true;}};
 const scope={window:{dogidoLibraryHasUnsaved:()=>true},dialog,header:title=>'<h2>'+title+'</h2>'};vm.createContext(scope);
 vm.runInContext(guard+'result=canReload();',scope);assert.equal(scope.result,false);assert.equal(shown,1);
 assert.match(dialog.innerHTML,/編集内容を残すため/);assert.match(dialog.innerHTML,/data-close-bookshelf/);assert.doesNotMatch(guard,/alert\(/);
 scope.window.dogidoLibraryHasUnsaved=()=>false;vm.runInContext('result=canReload();',scope);assert.equal(scope.result,true);assert.equal(shown,1);
});
test('standard native editing remains available without a custom clipboard module or author controls',()=>{
 const index=fs.readFileSync(__dirname+'/index.html','utf8'),controls=fs.readFileSync(__dirname+'/bookshelf-controls.js','utf8'),native=fs.readFileSync(__dirname+'/macos/main.swift','utf8');
 assert.doesNotMatch(index,/clipboard\.js|id="author"/);assert.doesNotMatch(controls,/decorateCopyButtons|data-copy-text|delete-current-poem/);
 assert.match(app,/const poemSections=\{all:'すべての句',favorites:'しおりの句'\}/);
 assert.match(native,/"copy:", "c"/);assert.match(native,/"paste:", "v"/);
});
