const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const M=require('./model.js');
test('newly refreshed poems can receive notes without recreating existing note state',()=>{
 const entries=[{id:'old'}],notes=M.createPlayerNotes(entries);
 notes.set('old','保持');entries.push({id:'new'});
 assert.equal(notes.set('new','新しい句'),true);assert.equal(notes.get('old'),'保持');
 entries.splice(0,1);assert.equal(notes.set('old','削除済み'),false);
});
const entries=[
 {id:'mob:sheep',category:'mob',raw:{label:'ヒツジ',poetic:{visual_tags:['もこもこ']},note:' 羊の原文\nそのまま '}},
 {id:'block:oak_log',category:'block',raw:{label:'オークの原木',note:'個別の説明'},contextNotes:[{label:'オーク',text:'どんぐりの木'}]},
 {id:'item:bread',category:'item',raw:{label:'パン'}}
];
const poems=[
 {id:'root:one',lines:['あさのやま','とりのこえする','もりのみち'],author:'ドギド',date:'2026-10-13',biomeId:'minecraft:forest',biome:'森林',timeOfDay:'morning',preface:'鳥がいるね。'},
 {id:'session:a:one',lines:['かわべりで','さかなをまって','ひがくれる'],author:'プレイヤー',date:'2026-09-12',biomeId:'minecraft:river',biome:'河川',timeOfDay:'evening',preface:null}
];
test('production model has no fabricated poems when bootstrap is absent',()=>assert.deepEqual(M.poems,[]));
test('catalog order, kana search, scoped context and original prose',()=>{
 assert.deepEqual(Object.keys(M.categories),['all','biome','structure','mob','block','item']);
 const before=JSON.stringify(entries);
 assert.equal(M.filterCatalog(entries,{query:'ﾋﾂｼﾞ'})[0].id,'mob:sheep');
 assert.equal(M.filterCatalog(entries,{query:'もこもこ'})[0].id,'mob:sheep');
 assert.equal(M.filterCatalog(entries,{category:'item',query:'ひつじ'}).length,0);
 assert.equal(M.filterCatalog(entries,{query:'どんぐり'})[0].id,'block:oak_log');
 assert.deepEqual(M.catalogBasicInfo(entries[1]),[{label:'説明',paragraphs:['個別の説明']},{label:'オークの共通説明',paragraphs:['どんぐりの木']}]);
 assert.equal(M.catalogBasicInfo(entries[0])[0].paragraphs[0],' 羊の原文\nそのまま ');
 assert.equal(JSON.stringify(entries),before);
});
test('biome descriptions retain scoped summaries but do not invent missing facts',()=>{
 const e={category:'biome',raw:{note:'景色',group_label:'温帯',group_snow_rule_summary:'山には雪',group_environment_rules:{precipitation:'雨が降る',water_behavior:'非表示'}}};
 assert.deepEqual(M.catalogBasicInfo(e),[{label:'説明',paragraphs:['景色']},{label:'温帯の共通説明',paragraphs:['山には雪','雨が降る']}]);
 assert.deepEqual(M.catalogBasicInfo({raw:{note:null,notes:[null,{}]}}),[]);
});
test('poem filters use source-qualified identity and recorded metadata',()=>{
 assert.equal(M.filterPoems(poems,{author:'プレイヤー',period:'2026-09'})[0].id,'session:a:one');
 assert.equal(M.filterPoems(poems,{biome:'minecraft:forest',timeOfDay:'morning'})[0].id,'root:one');
 assert.equal(M.filterPoems(poems,{query:'サカナ'})[0].id,'session:a:one');
 assert.equal(M.filterPoems(poems,{biome:'forest'}).length,0);
 const favorites=new Set(['root:one']);
 assert.equal(M.filterPoems(poems,{favoritesOnly:true,favorites}).length,1);
});
test('notes are isolated by source and preserve poem text',()=>{
 const before=JSON.stringify(poems),notes=M.createPlayerNotes(poems);
 assert(!notes.set('unknown','メモ'));
 assert(notes.set('root:one','<script>文字のまま</script>'));assert.equal(notes.get('session:a:one'),'');
 assert.equal(JSON.stringify(poems),before);
});
test('live bootstrap supplies poems and exact biome labels',()=>{
 const source=fs.readFileSync(__dirname+'/model.js','utf8'),context={window:{DOGIDO_BOOTSTRAP:{poems:structuredClone(poems),biomes:{'minecraft:forest':'森林','minecraft:river':'河川'},biomeEnglish:{'minecraft:forest':'Forest','minecraft:river':'River'}}}};
 vm.runInNewContext(source,context);
 const live=context.window.DogidoLibrary;
 assert.equal(live.poems.length,2);assert.equal(live.poems[0].biome,'森林');
 assert.equal(live.filterPoems(live.poems,{query:'Forest'})[0].id,'root:one');
 assert.equal(M.catalogEnglishName('mob:sheep'),'Sheep');
});
