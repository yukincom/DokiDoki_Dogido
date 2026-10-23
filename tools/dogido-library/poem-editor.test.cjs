const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {createDraft}=require('./poem-editor.js');
const original={id:'session:a:poem',revised:false,current:{id:'poem'},lines:['朝の山','とりのこえする','もりのみち']};
const saved=(request,id='revision-1')=>({...original,lines:request.lines,revised:true,current:{id}});
test('direct edit keeps exact text, target and original revision without rewriting the poem',()=>{
  const before=JSON.stringify(original),d=createDraft(original);assert.equal(d.hasChanges(),false);
  d.value=' 朝の山🌸 / ／\n自由な長さのことば\nもりのみち';
  const request=d.prepare(()=> 'one');
  assert.equal(request.expectedRevisionId,null);assert.deepEqual(request.expectedLines,original.lines);
  assert.deepEqual(request.lines,[' 朝の山🌸 / ／','自由な長さのことば','もりのみち']);
  assert.equal(JSON.stringify(original),before);assert.equal(d.hasChanges(),true);
});
test('save keeps edits made while waiting and undo after saving becomes an unsaved change',()=>{
  const d=createDraft(original);d.value='昼の山\nとりのこえする\nもりのみち';const request=d.prepare(()=> 'one');
  d.value='夜の山\nとりのこえする\nもりのみち';d.accept(saved(request));
  assert.equal(d.hasChanges(),true);assert.equal(d.value.split('\n')[0],'夜の山');
  const next=d.prepare(()=> 'two');assert.equal(next.expectedRevisionId,'revision-1');assert.deepEqual(next.expectedLines,request.lines);
  d.accept(saved(next,'revision-2'));assert.equal(d.hasChanges(),false);
  d.value=request.lines.join('\n');assert.equal(d.hasChanges(),true);
});
test('uncertain save retries the same request even if typing undoes to the old baseline',()=>{
  const d=createDraft(original);d.value='昼の山\nとりのこえする\nもりのみち';const request=d.prepare(()=> 'one');
  d.value=original.lines.join('\n');assert.equal(d.hasChanges(),true);
  assert.strictEqual(d.prepare(()=>{throw Error('must reuse request');}),request);
  d.accept(saved(request));assert.equal(d.hasChanges(),true);
  const undo=d.prepare(()=> 'two');assert.deepEqual(undo.lines,original.lines);assert.equal(undo.expectedRevisionId,'revision-1');
});
test('invalid three-line shape stays in the draft and can be corrected without guessing text',()=>{
  const d=createDraft(original);d.value='ひとつ\n';assert.throws(()=>d.prepare(()=> 'one'),/三行/);
  assert.equal(d.value,'ひとつ\n');assert.equal(d.hasChanges(),true);
  d.value='ひとつ\nふたつ\nみっつ';const request=d.prepare(()=> 'two');
  d.rejected();d.value='ひとつ\n修正\nみっつ';assert.equal(d.prepare(()=> 'three').requestId,'three');
  assert.equal(request.lines[1],'ふたつ');
});
test('readings save does not replace inputs and photo save skips identical value assignment',()=>{
  const controls=fs.readFileSync(__dirname+'/bookshelf-controls.js','utf8');
  const save=controls.slice(controls.indexOf('  async function save(){'),controls.indexOf('  window.DogidoReadings='));
  assert.doesNotMatch(save,/renderReadingsPage\(/);
  assert.match(fs.readFileSync(__dirname+'/photo-library.js','utf8'),/input\.value!==model\.title\(id\)/);
});
test('only a visible open poem editor blocks refresh, while hidden edits still count as unsaved',()=>{
  const source=fs.readFileSync(__dirname+'/poem-editor.js','utf8');
  let visible=true,dirty=false;
  const draft={open:true,input:{getClientRects:()=>visible?[{}]:[]},model:{hasChanges:()=>dirty}};
  const scope={root:{},drafts:new Map([['poem',draft]]),save(){}};
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('  root.DogidoPoemEditor='),source.indexOf("  document.addEventListener('dogido-detail-rendered'")),scope);
  assert.equal(scope.root.DogidoPoemEditor.isOpen(),true);
  visible=false;
  assert.equal(scope.root.DogidoPoemEditor.isOpen(),false);
  dirty=true;
  assert.equal(scope.root.DogidoPoemEditor.hasChanges(),true);
  scope.drafts.clear();
  assert.equal(scope.root.DogidoPoemEditor.isOpen(),false);
});
test('returning to an unchanged hidden editor uses the latest external revision and CAS',()=>{
  const source=fs.readFileSync(__dirname+'/poem-editor.js','utf8');
  const scope={createDraft,drafts:new Map(),head:p=>p.revised?p.current.id:null,changed(){},document:{createElement:()=>({setAttribute(){},addEventListener(){},getClientRects:()=>[]})}};
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('  function getDraft('),source.indexOf('  function decorate(')),scope);
  const first=scope.getDraft(original);first.open=true;
  const latest={...original,revised:true,current:{id:'external-1'},lines:['昼の山',...original.lines.slice(1)]};
  const returned=scope.getDraft(latest);
  assert.notStrictEqual(returned,first);assert.equal(returned.input.value,latest.lines.join('\n'));
  returned.model.value='夜の山\nとりのこえする\nもりのみち';
  const request=returned.model.prepare(()=> 'after-navigation');
  assert.equal(request.expectedRevisionId,'external-1');assert.deepEqual(request.expectedLines,latest.lines);
  // 非表示でも未保存の編集は捨てない。
  assert.strictEqual(scope.getDraft({...latest,current:{id:'external-2'}}),returned);
  // 表示中の未変更入力も、標準undoとカーソルを保持する。
  scope.drafts.clear();const visible=scope.getDraft(original);visible.open=true;
  visible.input.getClientRects=()=>[{}];assert.strictEqual(scope.getDraft(latest),visible);
});
test('save shortcut waits for composition and leaves undo/copy shortcuts to native inputs',async()=>{
  const source=fs.readFileSync(__dirname+'/live-library.js','utf8'),events={},callbacks=[];let saves=0;
  const scope={window:{},document:{addEventListener:(name,fn)=>events[name]=fn},saveButton:{addEventListener(){}},saveAll:()=>saves++,setTimeout:fn=>callbacks.push(fn)};
  vm.createContext(scope);vm.runInContext(source.slice(source.indexOf('  let composing=false'),source.indexOf("  window.addEventListener('beforeunload'")),scope);
  let prevented=0;const key=(key,shiftKey=false)=>events.keydown({metaKey:true,ctrlKey:false,altKey:false,shiftKey,key,preventDefault:()=>prevented++});
  key('s');assert.equal(saves,1);assert.equal(prevented,1);
  for(const k of ['z','c','v','x','a'])key(k);key('z',true);assert.equal(prevented,1);
  events.compositionstart();key('s');assert.equal(saves,1);events.compositionend();assert.equal(saves,1);
  callbacks.forEach(fn=>fn());assert.equal(saves,2);
});
