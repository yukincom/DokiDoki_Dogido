const test=require('node:test');
const assert=require('node:assert/strict');
const P=require('./photo-model.js');
const catalog={book:'catalog',id:'sheep',view:'catalog'};
test('photo attaches to one entry without altering another entry or a poem',()=>{
  const a=P.createAlbum();a.attach(catalog,'sample-day');
  assert.equal(a.get(catalog).id,'sample-day');
  assert.equal(a.get({...catalog,id:'allay'}),null);
  assert.equal(a.get({book:'poems',id:'sheep',view:'landscape'}),null);
});
test('landscape and player photos share a three-slot album while preserving framing metadata',()=>{
  const a=P.createAlbum(),t={book:'poems',id:'demo-1',view:'landscape'};
  a.attach(t,'sample-day');a.attach({...t,view:'player'},'sample-night');
  assert.equal(a.list(t).length,2);assert.equal(a.get(t).id,'sample-night');
  assert.equal(a.list(t)[0].view,'landscape');assert.equal(a.list(t)[1].view,'player');
  a.attach(t,'sample-third');assert.equal(a.list({...t,view:'player'}).length,3);
});
test('restore removes only the current reference, not images or other entries',()=>{
  const a=P.createAlbum(),other={...catalog,id:'allay'};
  a.attach(catalog,'sample-day');a.attach(other,'sample-night');a.clear(catalog);
  assert.equal(a.get(catalog),null);assert.equal(a.get(other).id,'sample-night');assert.equal(P.samples.length,3);
});
test('new page memory starts empty, no shared persistent state',()=>{
  const a=P.createAlbum();a.attach(catalog,'sample-day');assert.equal(P.createAlbum().get(catalog),null);
});
test('unknown result and invalid target are rejected without overwriting an existing photo',()=>{
  const a=P.createAlbum();a.attach(catalog,'sample-day');
  assert.throws(()=>a.attach(catalog,'not-a-photo'));assert.equal(a.get(catalog).id,'sample-day');
  assert.throws(()=>a.attach({...catalog,view:'player'},'sample-night'));
  assert.throws(()=>a.get(null));
});
test('three photos can be collected and switched, repeated selection does not duplicate',()=>{
  const a=P.createAlbum();for(const p of P.samples)a.attach(catalog,p.id);
  assert.equal(a.list(catalog).length,3);a.attach(catalog,'sample-day');assert.equal(a.list(catalog).length,3);
  a.select(catalog,'sample-night');assert.equal(a.get(catalog).id,'sample-night');
  a.remove(catalog,'sample-night');assert.equal(a.list(catalog).length,2);assert.equal(a.get(catalog).id,'sample-day');
  assert.throws(()=>a.select(catalog,'sample-night'));
  const list=a.list(catalog);list.pop();assert.equal(a.list(catalog).length,2);
  a.attach(catalog,'sample-night');assert.equal(a.list(catalog).length,3);
});
