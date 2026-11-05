const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const script=fs.readFileSync(__dirname+'/photo-library.js','utf8'),source=script.slice(script.indexOf('(function photoAvailability(){'));
function fixture(boot,fetch){
  const handlers={},notes=[];
  const scope={window:{DOGIDO_BOOTSTRAP:boot},WeakMap,AbortController,setTimeout,clearTimeout,fetch,
    document:{addEventListener:(name,handler)=>handlers[name]=handler,createElement:()=>{
      const node={isConnected:true,setAttribute(k,v){this[k]=v;},remove(){this.isConnected=false;}};notes.push(node);return node;
    }}};
  vm.runInNewContext(source,scope);
  const classes=new Set(),img={tagName:'IMG',isConnected:true,getAttribute:()=>'/api/photos/test/image',closest:()=>null,
    classList:{add:n=>classes.add(n),remove:n=>classes.delete(n)},after(){}};
  return {handlers,notes,img,classes};
}
// 見込み: 遠隔本棚の接続喪失を写真枠へ表示し、復帰した画像は案内を消す。
// 合格: 503では未接続案内、load後は画像を隠さず案内なし。範囲: 実イベント処理・模擬DOM/HTTP。
test('remote photo failure explains disconnection in its frame and successful load clears it',async()=>{
  const f=fixture({parent_view:true},async()=>({status:503,ok:false}));
  await f.handlers.error({target:f.img});assert.match(f.notes[0].textContent,/子機とつながっていません/);
  assert(f.classes.has('photo-load-failed'));
  f.handlers.load({target:f.img});assert(!f.notes[0].isConnected);assert(!f.classes.has('photo-load-failed'));
});
// 見込み: 原画像欠損を通信切れと誤案内せず、子のローカル画像には親との接続を要求しない。
// 合格: 404は保存場所案内、子の失敗はドギド口調で再照会なし。範囲: 分岐とイベント処理。
test('missing original and local child photo do not claim a missing family connection',async()=>{
  const f=fixture({parent_view:true},async()=>({status:404,ok:false}));
  await f.handlers.error({target:f.img});assert.match(f.notes[0].textContent,/保存場所/);assert.doesNotMatch(f.notes[0].textContent,/つながっていません/);
  const c=fixture({child_mode:true},()=>{throw Error('local photos need no parent probe');});
  await c.handlers.error({target:c.img});assert.match(c.notes[0].textContent,/確認してな/);
});
// 見込み: 写真の成功後に遅れて返った接続エラーで表示を再び隠さない。
// 合格: 先行load後の503では案内が復活しない。範囲: 非同期の応答順を制御する。
test('late failure probe cannot replace an image that has already loaded',async()=>{
  let resolve;const f=fixture({parent_view:true},()=>new Promise(r=>resolve=r));
  const task=f.handlers.error({target:f.img});f.handlers.load({target:f.img});
  resolve({status:503,ok:false});await task;
  assert(!f.classes.has('photo-load-failed'));assert(!f.notes[0].isConnected);
});
