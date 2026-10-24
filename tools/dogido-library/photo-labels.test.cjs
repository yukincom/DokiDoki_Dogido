const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {create}=require('./photo-library-model.js');
const source=fs.readFileSync(__dirname+'/photo-library.js','utf8');
const esc=value=>String(value??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
function fixture(retained=false){
 const row={id:'original',protected:true,filename:'dogido_2026-10-21_13.53.57.png',url:'/api/photos/original/image',archiveStatus:{state:'pending'}};
 const model=create({revision:0,items:[row],placements:{'poems:p':retained?[null,null,null]:['original',null,null]},originals:{'poems:p':'original'}});
 let html='';
 const stage={querySelector:()=>null,querySelectorAll:()=>[],classList:{toggle(){}},prepend(){}};
 const overview={after:area=>html=area.innerHTML};
 const detail={querySelector:key=>key==='.poem-overview'?overview:stage,querySelectorAll:()=>[]};
 const scope={model,esc,boot:{photos:{}},disabled:()=>'',activePages:new Map(),state:{book:'poems'},current:()=>({id:'p'}),pageTarget:()=> 'poems:p',renderPoemThumbnails(){},$:()=>detail,
   document:{createElement:()=>({setAttribute(){}})}};
 vm.createContext(scope);
 vm.runInContext(source.slice(source.indexOf('  function playerPhotoLabel('),source.indexOf('  function related('))+source.slice(source.indexOf('  function renderPlaced('),source.indexOf('  // 右頁'))+'renderPlaced();',scope);
 return {html,model,scope};
}
for(const retained of [false,true])test('original photo label is quiet and protected, retained='+retained,()=>{
 const {html,model}=fixture(retained);
 assert.match(html,/ドギドの視点/);
 assert.doesNotMatch(html,/研究原本|変更・削除不可|保全待ち|dogido_2026|placed-photo-title|data-photo-title/);
 assert.doesNotMatch(html,/data-detach-slot="0"|data-delete-photo/);
 assert.equal(model.hasChanges(),false);assert.equal(model.find('original').filename,'dogido_2026-10-21_13.53.57.png');
});
