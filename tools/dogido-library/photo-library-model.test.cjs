const test=require('node:test');const assert=require('node:assert/strict');
const {create}=require('./photo-library-model.js');
const fixture=()=>({revision:2,items:[{id:'a',filename:'20260917_01.png',title:''},{id:'b',filename:'20260917_02.png',title:'雲'}],placements:{}});
test('live refresh only accepts clean state and clears reverted drafts before new automatic placement',()=>{
  const model=create(fixture());
  model.attach('poems:p',0,'a');assert.equal(model.refresh({...fixture(),revision:3}),false);
  model.detach('poems:p',0);assert.equal(model.hasChanges(),false);
  const next={...fixture(),revision:4,placements:{'poems:p':['b',null,null]}};
  assert.equal(model.refresh(next),true);assert.equal(model.mainPhoto('poems:p').id,'b');
  assert.equal(model.hasChanges(),false);assert.equal(model.refresh(fixture()),false);
  model.rename('b','編集中');assert.equal(model.refresh({...next,revision:5}),false);
  assert.equal(model.title('b'),'編集中');
});
test('main photo uses the displayed slot, falls back to an existing photo and never edits state',()=>{
  const data=fixture();data.placements={'poems:p':['missing','a','b'],'poems:q':['b',null,null]};const before=JSON.stringify(data),model=create(data);
  assert.equal(model.mainPhoto('poems:p').id,'a');assert.equal(model.mainPhoto('poems:p','b').id,'b');
  assert.equal(model.mainPhoto('poems:q','a').id,'b');assert.equal(model.mainPhoto('poems:none','a'),null);
  assert.equal(JSON.stringify(data),before);assert.equal(model.hasChanges(),false);
  model.detach('poems:p',2);assert.equal(model.mainPhoto('poems:p','b').id,'a');
  model.detach('poems:p',1);assert.equal(model.mainPhoto('poems:p','b'),null);
});
test('title is shared across pages; empty titles use the extension-free filename',()=>{
  const model=create(fixture());assert.equal(model.displayTitle('a'),'20260917_01');
  model.attach('catalog:mob:creeper',0,'a');model.attach('poems:poem-1',1,'a');
  model.rename('a','ふわふわ');assert.equal(model.all()[0].displayTitle,'ふわふわ');
  assert.deepEqual(model.targets('a'),['catalog:mob:creeper','poems:poem-1']);
  model.rename('a','');assert.equal(model.displayTitle('a'),'20260917_01');
});
test('detaching only changes that page and leaves the original plus other placements',()=>{
  const model=create(fixture());model.attach('poems:p1',0,'a');model.attach('poems:p2',1,'a');
  model.detach('poems:p1',0);assert.ok(model.find('a'));assert.equal(model.slots('poems:p2')[1],'a');
  assert.equal(model.attach('poems:p2',2,'a'),false);assert.equal(model.attach('poems:p2',3,'b'),false);
});
test('accepting a save keeps edits made while the request was pending',()=>{
  const model=create(fixture());model.rename('a','最初');model.attach('poems:p',0,'a');const sent=model.changes();
  model.rename('a','あとから');model.attach('poems:p',1,'b');
  model.accept({revision:3,items:[{...fixture().items[0],title:'最初'},fixture().items[1]],placements:{'poems:p':['a',null,null]}},sent);
  assert.equal(model.displayTitle('a'),'あとから');assert.equal(model.changes().revision,3);assert.equal(model.slots('poems:p')[1],'b');
});
test('newly ingested photos retain pending title and placement edits',()=>{
  const model=create(fixture());model.rename('a','編集中');model.attach('poems:p',0,'a');
  model.accept({...fixture(),revision:3,items:[...fixture().items,{id:'c',filename:'20260917_03.png',title:''}]});
  assert.equal(model.displayTitle('a'),'編集中');assert.equal(model.slots('poems:p')[0],'a');assert.ok(model.find('c'));
});
test('late save and import responses never drop a more recent photo',()=>{
  const model=create(fixture());model.rename('a','保存したタイトル');const sent=model.changes();
  const saved={...fixture(),revision:3,items:[{...fixture().items[0],title:'保存したタイトル'},fixture().items[1]]};
  model.accept({...saved,revision:4,items:[...saved.items,{id:'new',filename:'20260917_03.png',title:''}]});
  model.accept(saved,sent);assert.ok(model.find('new'));assert.equal(model.changes().revision,4);assert.equal(model.hasChanges(),false);
  model.accept(fixture());assert.ok(model.find('new'));assert.equal(model.changes().revision,4);
});
