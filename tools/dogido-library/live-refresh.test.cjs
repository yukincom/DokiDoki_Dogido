/* 実際のpoll関数を模擬DOMで実行し、取得途中の入力を上書きしないことを確認する。 */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(__dirname+'/live-library.js','utf8');
function fixture(){
  let resolve;const scope={
    window:{dogidoLibraryHasUnsaved:()=>false,DogidoPhotos:{refresh:()=>true}},
    document:{hidden:false,activeElement:null,querySelector:()=>null,addEventListener(){}},
    boot:{csrf:'token',photos:{revision:1}},revision:1,
    fetch:()=>new Promise(yes=>resolve=yes),setInterval(){},
    M:{poems:[],biomes:{}},state:{favorites:new Set(),period:'all'},
    playerNotes:{set(){}},liveDrafts:new Map(),$:()=>({innerHTML:'',value:''}),esc:String,
    catalogNotes:{reload(){}},
    renderNavigation(){},renderList(){},placeBookCover(){},dirtyStatus(){}
  };
  vm.createContext(scope);
  vm.runInContext(source.slice(source.indexOf('  let refreshing=false'),source.lastIndexOf('})();')),scope);
  return {scope,start:()=>vm.runInContext('refresh()',scope),resolve:(revision=2)=>resolve({ok:true,json:async()=>({
    player:{revision,poems:{},catalog:{}},poems:[{id:'new',date:'2026-10-20'}],
    photos:{revision,items:[],placements:{}}})})};
}
test('clean polling adds saved poems',async()=>{
 const f=fixture(),p=f.start();f.resolve();await p;assert.equal(f.scope.M.poems[0].id,'new');
});
for(const reason of ['input','dirty','dialog','hidden'])test('pending refresh defers if '+reason+' begins during fetch',async()=>{
 const f=fixture(),p=f.start();
 if(reason==='input')f.scope.document.activeElement={tagName:'TEXTAREA'};
 if(reason==='dirty')f.scope.window.dogidoLibraryHasUnsaved=()=>true;
 if(reason==='dialog')f.scope.document.querySelector=()=>({});
 if(reason==='hidden')f.scope.document.hidden=true;
 f.resolve();await p;assert.equal(f.scope.M.poems.length,0);
});
test('old response cannot undo a completed save',async()=>{
 const f=fixture(),p=f.start();f.scope.revision=5;f.resolve(2);await p;assert.equal(f.scope.M.poems.length,0);
});
