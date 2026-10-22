const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const N=require('./catalog-player-notes.js');
const entries=[{category:'mob',id:'sheep',raw:{note:'原本'}},{category:'item',id:'sheep'},{category:'biome',id:'snowy_plains'}];
function storage(){const values=new Map();return {getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)};}
test('ten-key limit, edits, deletion and reload keep original catalog untouched',()=>{
  const s=storage(),before=JSON.stringify(entries),n=N.create(entries,s),id='mob:sheep';
  assert.equal(n.add(id,'  '),false);
  for(let i=0;i<10;i++)assert.equal(n.add(id,`言葉${i}`),true);
  assert.equal(n.add(id,'11個目'),false);
  assert.equal(n.edit(id,0,'ふわふわ'),true);
  assert.equal(n.remove(id,4),true);assert.equal(n.add(id,'もこもこ'),true);
  n.addReading(id,'羊','ひつじ');
  assert.deepEqual(N.create(entries,s).get(id),n.get(id));
  assert.deepEqual(n.get('item:sheep').keys,[]);
  assert.deepEqual(n.get('biome:snowy_plains').keys,[]);
  assert.equal(JSON.stringify(entries),before);
  const copy=n.get(id);copy.keys.push('外から変更');assert.equal(n.get(id).keys.length,10);
  n.removeReading(id,0);assert.deepEqual(N.create(entries,s).get(id).readings,[]);
});
test('invalid identities and indices cannot change records',()=>{
  const n=N.create(entries,storage());n.add('mob:sheep','雪');
  for(const i of [-1,1,0.1,NaN]){assert.equal(n.remove('mob:sheep',i),false);assert.equal(n.edit('mob:sheep',i,'変更'),false);}
  assert.equal(n.add('mob:missing','無効'),false);assert.equal(n.addReading('mob:sheep','羊',null),false);
  assert.deepEqual(n.get('mob:sheep').keys,['雪']);
});
test('unavailable storage preserves session edits with explicit failure status',()=>{
  const n=N.create(entries,{getItem(){throw Error('denied');},setItem(){throw Error('full');}});
  n.add('mob:sheep','綿');n.addReading('mob:sheep','羊','ひつじ');
  assert.deepEqual(n.get('mob:sheep'),{keys:['綿'],readings:[{word:'羊',reading:'ひつじ'}],saved:false});
});
test('malformed saved data does not crash or overwrite on read',()=>{
  for(const bad of ['{','null','{"keys":{},"reading":""}',JSON.stringify({keys:Array(11).fill('雪'),reading:''})]){
    const s=storage();s.setItem(N.PREFIX+'mob:sheep',bad);
    const n=N.create(entries,s);assert.equal(n.get('mob:sheep').saved,false);
    assert.equal(s.getItem(N.PREFIX+'mob:sheep'),bad);
  }
});
test('notes UI escapes player text and reflects the limit',()=>{
  const s=storage(),catalogNotes=N.create(entries,s),id='mob:sheep';
  catalogNotes.add(id,'<img src=x onerror=alert(1)>');catalogNotes.addReading(id,'<言葉>','"<&');
  const app=fs.readFileSync(`${__dirname}/app.js`,'utf8');
  const render=app.slice(app.indexOf('const catalogNoteId='),app.indexOf('const indexPanel='));
  const escape=app.slice(app.indexOf('const esc='),app.indexOf('const glyphs='));
  const scope={catalogNotes,e:entries[0]};vm.createContext(scope);
  vm.runInContext(escape+render+'\nresult=renderCatalogNotes(e);',scope);
  assert.match(scope.result,/ドギドに覚えておいてほしいことを記入してください。/);
  assert.match(scope.result,/&lt;img/);assert.doesNotMatch(scope.result,/<img/);
  assert.match(scope.result,/&quot;&lt;&amp;/);
  assert.match(scope.result,/<label>漢字<input/);assert.match(scope.result,/<label>読み仮名<input/);
  assert.match(scope.result,/aria-label="読み訂正を追加"/);assert.match(scope.result,/aria-label="読み訂正 1 を削除"/);
  for(let i=1;i<10;i++)catalogNotes.add(id,'雪');
  vm.runInContext('result=renderCatalogNotes(e);',scope);
  assert.match(scope.result,/id="catalog-key-new"[^>]*disabled/);
  assert.match(scope.result,/10個まで追加できます（10 \/ 10）/);
});
test('reading pairs require both values, edit independently and persist deletion',()=>{
  const s=storage(),n=N.create(entries,s),id='mob:sheep';
  assert.equal(n.addReading(id,' ','くさち'),false);assert.equal(n.addReading(id,'草地',' '),false);
  n.addReading(id,'草地','くさち');n.addReading(id,'羊','ひつじ');
  n.editReading(id,0,'reading','そうち');
  assert.deepEqual(n.get(id).readings,[{word:'草地',reading:'そうち'},{word:'羊',reading:'ひつじ'}]);
  for(const i of [-1,2,0.5]){assert.equal(n.removeReading(id,i),false);assert.equal(n.editReading(id,i,'word','無効'),false);}
  assert.equal(n.editReading(id,0,'other','無効'),false);
  assert.deepEqual(n.get('item:sheep').readings,[]);
  const copy=n.get(id);copy.readings[0].word='外部変更';assert.equal(n.get(id).readings[0].word,'草地');
  n.removeReading(id,0);assert.deepEqual(N.create(entries,s).get(id).readings,[{word:'羊',reading:'ひつじ'}]);
});
test('legacy reading survives without inventing the target word',()=>{
  const s=storage(),id='mob:sheep',old=JSON.stringify({keys:['綿'],reading:'以前の読み訂正'});s.setItem(N.PREFIX+id,old);
  const n=N.create(entries,s);assert.deepEqual(n.get(id).readings,[{word:'',reading:'以前の読み訂正'}]);
  assert.equal(s.getItem(N.PREFIX+id),old);
  n.editReading(id,0,'word','対象');assert.deepEqual(N.create(entries,s).get(id).readings,[{word:'対象',reading:'以前の読み訂正'}]);
});
test('clean refresh drops cached saved values without losing an unadded draft',()=>{
  let raw=JSON.stringify({keys:['旧'],readings:[]});
  const model=N.create([{category:'mob',id:'sheep'}],{getItem:()=>raw,setItem:(_k,v)=>{raw=v;}});
  assert.deepEqual(model.get('mob:sheep').keys,['旧']);
  model.setDraft('mob:sheep','途中');raw=JSON.stringify({keys:['新'],readings:[]});model.reload();
  assert.deepEqual(model.get('mob:sheep').keys,['新']);assert.equal(model.getDraft('mob:sheep'),'途中');
});
