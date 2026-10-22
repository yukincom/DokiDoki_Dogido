const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const app=fs.readFileSync(__dirname+'/app.js','utf8');
function coverScope(){
 const classes=new Set(),nodes=new Map(),moves=[],routes=[],opened=[],body={dataset:{},classList:{add:c=>classes.add(c),remove:c=>classes.delete(c),contains:c=>classes.has(c)}};
 const $=id=>{if(!nodes.has(id))nodes.set(id,{hidden:true});return nodes.get(id);};
 const scope={$,document:{body,querySelectorAll:()=>[],querySelector:selector=>({append:node=>moves.push({selector,node})})},history:{replaceState:(_a,_b,route)=>routes.push(route)},switchBook:book=>opened.push(book)};
 vm.createContext(scope);vm.runInContext(app.slice(app.indexOf('function showBookCover('),app.indexOf('function switchBook(')),scope);
 return {scope,nodes,moves,routes,opened,body,classes};
}
test('all three book covers reuse the real menu, move only the cover, and enter contents directly',()=>{
 for(const book of ['poems','catalog','photos']){
  const c=coverScope();vm.runInContext(`showBookCover('${book}');`,c.scope);
  assert.deepEqual(c.opened,[book]);assert(c.classes.has('book-intro'));
  assert.equal(c.nodes.get('book-cover').hidden,false);assert.equal(c.body.dataset.coverBook,book);
  assert.equal(c.moves[0].selector,book==='photos'?'.album-preview':'.pages');
  assert.equal(c.moves[0].node,c.nodes.get('book-cover'));assert.equal(c.routes.at(-1),`#${book}-cover`);
  vm.runInContext('closeBookCover();',c.scope);assert.equal(c.nodes.get('book-cover').hidden,true);assert(!c.classes.has('book-intro'));assert.equal(c.routes.at(-1),`#${book}`);
  const count=c.routes.length;vm.runInContext('closeBookCover();',c.scope);assert.equal(c.routes.length,count);
 }
});
test('unsupported cover does not alter the current view',()=>{
 const c=coverScope();vm.runInContext("showBookCover('unknown');",c.scope);assert.equal(c.routes.length,0);assert.equal(c.opened.length,0);assert.equal(c.moves.length,0);
});
test('album redraw detaches the shared cover before replacing the album DOM',()=>{
 const source=fs.readFileSync(__dirname+'/photo-library.js','utf8'),fn=source.slice(source.indexOf('  function renderAlbum(){'),source.indexOf('  function updateTitleFields('));
 const actions=[],cover={},page={contains:el=>el===cover,before:el=>actions.push(['detach',el]),set innerHTML(value){actions.push(['render',value]);}};
 const scope={window:{},page,$:()=>cover,selected:null,model:{all:()=>[]},document:{dispatchEvent:e=>actions.push(['event',e.type])},Event:class{constructor(type){this.type=type;}}};vm.createContext(scope);vm.runInContext(source.slice(source.indexOf('  function photoFolderButton(){'),source.indexOf('  function pageTarget(){'))+fn+'renderAlbum();',scope);
 assert.equal(actions[0][0],'detach');assert.equal(actions[0][1],cover);assert.equal(actions[1][0],'render');assert.match(actions[1][1],/album-sidebar/);assert.equal(actions[2][1],'dogido-album-rendered');
});
test('header has no extra open/clipboard controls and retains one versioned scroll image',()=>{
 const html=fs.readFileSync(__dirname+'/index.html','utf8'),controls=fs.readFileSync(__dirname+'/bookshelf-controls.js','utf8'),css=fs.readFileSync(__dirname+'/cover.css','utf8');
 assert.doesNotMatch(html,/book-cover-open|data-open-book|clipboard\.js/);assert.match(html,/assets\/bookshelf-open-scroll-v4\.png/);
 assert.doesNotMatch(controls,/actions\.append|const refresh=|const trashButton=|data-copy-text/);
 assert.match(controls,/poemActions\.append\(deletePoemsButton,restoreButton\)/);assert.match(controls,/restoreButton\.setAttribute\('aria-label','復元'\)/);
 assert.doesNotMatch(css,/\.book-intro \.desk[^\n]*display:none/);assert.match(app,/if\(b\.dataset\.category\|\|b\.dataset\.poemGroup\|\|b\.dataset\.entry\)closeBookCover\(\)/);
});
