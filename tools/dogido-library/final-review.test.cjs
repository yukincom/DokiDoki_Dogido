/* 画面移動中の入力と非同期ごみ箱応答の回帰検査。 */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const N=require('./catalog-player-notes.js');
const app=fs.readFileSync(__dirname+'/app.js','utf8'),live=fs.readFileSync(__dirname+'/live-library.js','utf8'),controls=fs.readFileSync(__dirname+'/bookshelf-controls.js','utf8');
function notes(){const values=new Map();return N.create([{category:'mob',id:'sheep'},{category:'item',id:'sheep'}],{getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)});}
test('unadded catalog input survives another item and is cleared only by successful addition',()=>{
 const n=notes();n.setDraft('mob:sheep','  <ことば>  ');n.setDraft('item:sheep','別項目');
 assert.equal(n.getDraft('mob:sheep'),'  <ことば>  ');assert.equal(n.hasDrafts(),true);assert.deepEqual(n.get('mob:sheep').keys,[]);
 assert.equal(n.setDraft('unknown','無効'),false);assert.equal(n.setDraft('mob:sheep',null),false);
 assert.equal(n.add('mob:sheep','  '),false);assert.equal(n.getDraft('mob:sheep'),'  <ことば>  ');
 assert.equal(n.add('mob:sheep',n.getDraft('mob:sheep')),true);assert.equal(n.getDraft('mob:sheep'),'');assert.deepEqual(n.get('mob:sheep').keys,['<ことば>']);
 assert.equal(n.getDraft('item:sheep'),'別項目');n.setDraft('item:sheep','');assert.equal(n.hasDrafts(),false);
 for(let i=1;i<10;i++)n.add('mob:sheep','語');n.setDraft('mob:sheep','まだ追加しない');
 assert.equal(n.add('mob:sheep','まだ追加しない'),false);assert.equal(n.getDraft('mob:sheep'),'まだ追加しない');
});
test('catalog draft is restored escaped and remains unsaved while its input is not mounted',()=>{
 const catalogNotes=notes();catalogNotes.setDraft('mob:sheep','"<未追加>');
 const scope={catalogNotes,e:{category:'mob',id:'sheep'},$:()=>null};vm.createContext(scope);
 const render=app.slice(app.indexOf('const catalogNoteId='),app.indexOf('const indexPanel='));
 const escape=app.slice(app.indexOf('const esc='),app.indexOf('const glyphs='));
 vm.runInContext(escape+render+live.slice(live.indexOf('  function unadded(){'),live.indexOf('  const photoDirty='))+'html=renderCatalogNotes(e);dirty=unadded();',scope);
 assert.match(scope.html,/id="catalog-key-new" value="&quot;&lt;未追加&gt;"/);assert.equal(scope.dirty,true);
 catalogNotes.setDraft('mob:sheep','');vm.runInContext('dirty=unadded();',scope);assert.equal(scope.dirty,false);
 assert.match(app,/catalogNotes\.setDraft\(catalogNoteId\(current\(\)\),event\.target\.value\)/);
});
function trashScope(){
 let nodes,resolve,reject;
 const dialog={dataset:{},open:false,showModal(){this.open=true;},get innerHTML(){return '';},set innerHTML(_html){if(nodes)nodes.status.isConnected=false;nodes={status:{isConnected:true,textContent:'読み込み中…'},list:{innerHTML:''}};},querySelector:s=>s==='[data-dialog-status]'?nodes.status:nodes.list};
 const scope={dialog,header:()=>'',esc:String,request:()=>new Promise((yes,no)=>{resolve=yes;reject=no;})};vm.createContext(scope);
 vm.runInContext(controls.slice(controls.indexOf('  async function showTrash(){'),controls.indexOf('  function confirmDelete(')),scope);
 return {scope,dialog,start(){return vm.runInContext('showTrash()',scope);},nodes:()=>nodes,resolve:value=>resolve(value),reject:error=>reject(error)};
}
for(const outcome of ['success','failure'])test('stale trash '+outcome+' never touches a replacement dialog',async()=>{
 const c=trashScope(),pending=c.start(),old=c.nodes();c.dialog.dataset.mode='unsaved';c.dialog.innerHTML='other screen';const current=c.nodes();
 if(outcome==='success')c.resolve({items:[]});else c.reject(Error('late failure'));
 await pending;assert.equal(old.status.textContent,'読み込み中…');assert.equal(current.status.textContent,'読み込み中…');assert.equal(current.list.innerHTML,'');
});
test('closed trash ignores its result and a current trash still renders normally',async()=>{
 const c=trashScope(),pending=c.start();c.dialog.open=false;c.resolve({items:[]});await pending;assert.equal(c.nodes().status.textContent,'読み込み中…');
 const next=c.start();c.resolve({items:[]});await next;assert.equal(c.nodes().status.textContent,'ごみ箱は空です。');
});
test('newer trash load wins over an older load in the same dialog mode',async()=>{
 const c=trashScope(),pending=c.start();c.scope.request=()=>Promise.resolve({items:[]});await c.start();
 c.resolve({items:[{kind:'poem',label:'古い結果',expiresAt:0,id:'old'}]});await pending;
 assert.equal(c.nodes().status.textContent,'ごみ箱は空です。');assert.equal(c.nodes().list.innerHTML,'');
});
