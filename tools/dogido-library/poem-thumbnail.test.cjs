const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {create}=require('./photo-library-model.js');
const source=fs.readFileSync(__dirname+'/photo-library.js','utf8');
test('list thumbnail follows the same main image, updates on switching, and clears on detach',()=>{
 const model=create({revision:0,items:[{id:'a',url:'/api/photos/a/image'},{id:'b',url:'/api/photos/b/image'}],placements:{'poems:root:p':['a','b',null]}}),activePages=new Map();
 const thumbs=['root:p','root:empty'].map(id=>({dataset:{poemThumbnail:id},children:[],replaceChildren(){this.children=[];},append(img){this.children.push(img);}}));
 const scope={model,activePages,document:{querySelectorAll:selector=>{assert.equal(selector,'[data-poem-thumbnail]');return thumbs;},createElement:tag=>{assert.equal(tag,'img');return {};}}};vm.createContext(scope);
 vm.runInContext(source.slice(source.indexOf('  function renderPoemThumbnails('),source.indexOf('  function renderPicker('))+'renderPoemThumbnails();',scope);
 assert.equal(thumbs[0].children[0].src,'/api/photos/a/image');assert.equal(thumbs[0].children[0].alt,'');assert.equal(thumbs[0].children[0].loading,'lazy');assert.deepEqual(thumbs[1].children,[]);assert.equal(model.hasChanges(),false);
 activePages.set('poems:root:p','b');vm.runInContext('renderPoemThumbnails();',scope);assert.equal(thumbs[0].children[0].src,'/api/photos/b/image');assert.equal(thumbs[0].children.length,1);
 model.detach('poems:root:p',1);vm.runInContext('renderPoemThumbnails();',scope);assert.equal(thumbs[0].children[0].src,'/api/photos/a/image');
 model.detach('poems:root:p',0);vm.runInContext('renderPoemThumbnails();',scope);assert.deepEqual(thumbs[0].children,[]);
});
test('thumbnail is a small square CSS crop and detail changes refresh it without a separate save path',()=>{
 const css=fs.readFileSync(__dirname+'/poem-metadata.css','utf8');
 assert.match(css,/\.poem-list-thumbnail\{[^}]*flex:0 0 40px;[^}]*width:40px;height:40px;/);
 assert.match(css,/\.poem-list-thumbnail img\{[^}]*object-fit:cover;object-position:center/);
 assert.match(source,/function renderPlaced\(\)\{\s*renderPoemThumbnails\(\)/);assert.match(source,/const active=model\.mainPhoto\(target,activePages\.get\(target\)\)\?\.id/);
});
