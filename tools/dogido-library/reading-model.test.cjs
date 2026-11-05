const test=require('node:test'),assert=require('node:assert/strict'),M=require('./reading-model.js');
// 見込み: 読み訂正のdraftだけが保存要求を作る。合格: CASと未保存編集を保ち、閲覧専用では変更しない。
// 範囲: JSの状態モデル。HTTPの拒否、実ブラウザー表示、読み上げ品質は別試験。
test('saved reading keeps compare-and-swap identity, including retries',()=>{
 const m=M.create([{id:'c1',surface:'草地',reading:'そうち'}]);const k=m.rows()[0].key;m.edit(k,'くさち');
 const edit=m.changes()[0];assert.equal(edit.body.expected_id,'c1');
 m.accept(edit,{correction:{id:'c2',surface:'草地',reading:'くさち'}});
 assert.equal(m.hasChanges(),false);m.edit(k,'くさば');assert.equal(m.changes()[0].body.expected_id,'c2');
});
test('edits during saving are retained, duplicates and invalid kana rejected',()=>{
 const m=M.create();const k=m.add('草地','くさち'),edit=m.changes()[0];m.edit(k,'くさば');
 m.accept(edit,{correction:{id:'c1',surface:'草地',reading:'くさち'}});
 assert.equal(m.changes()[0].body.reading,'くさば');assert.throws(()=>m.add('草地','くさち'));
 assert.throws(()=>m.add('山','ヤマ'));m.remove(k);assert.equal(m.changes()[0].method,'DELETE');
});

test('reading list is in Japanese reading order, independent of surface and creation date',()=>{
 const m=M.create([{id:'c3',surface:'山',reading:'やま',created_at:'2020-01-01'},{id:'c1',surface:'朝',reading:'あさ',created_at:'2030-01-01'},{id:'c2',surface:'草地',reading:'くさち'}]);
 assert.deepEqual(m.rows().map(r=>r.reading),['あさ','くさち','やま']);
 assert.ok(m.rows().every(r=>!Object.hasOwn(r,'created_at')));
 m.add('池','いけ');assert.deepEqual(m.rows().map(r=>r.reading),['あさ','いけ','くさち','やま']);
});
test('one selected batch stages only checked readings and preserves CAS on each deletion',()=>{
 const m=M.create([{id:'c1',surface:'朝',reading:'あさ'},{id:'c2',surface:'草地',reading:'くさち'},{id:'c3',surface:'山',reading:'やま'}]);
 const keys=m.rows().filter(r=>r.surface!=='草地').map(r=>r.key);
 assert.equal(m.removeMany([...keys,keys[0]]),2);
 assert.deepEqual(m.rows().map(r=>r.surface),['草地']);
 assert.deepEqual(m.changes().map(e=>[e.method,e.body.expected_id]),[['DELETE','c1'],['DELETE','c3']]);
 for(const edit of m.changes())m.accept(edit,{removed:true});
 assert.equal(m.hasChanges(),false);
});

// 見込み: 子機の読み一覧は表示だけに使い、古い操作イベントでも編集を作らない。
// 合格: 追加・編集・削除・保存応答を渡しても行とdirty状態が不変。範囲: 状態モデルのみ。
test('shared readings remain unchanged after every local mutation entry point',()=>{
 const records=[{id:'host-1',surface:'草地',reading:'くさち'}],m=M.create(records,false);
 const before=m.rows(),key=before[0].key;
 assert.equal(m.writable,false);
 assert.equal(m.add('山','やま'),null);m.edit(key,'そうち');
 assert.equal(m.remove(key),false);assert.equal(m.removeMany([key]),0);
 m.accept({method:'PUT',key},{correction:{id:'forged',surface:'草地',reading:'そうち'}});
 m.accept({method:'DELETE',key},{});
 assert.deepEqual(m.rows(),before);assert.deepEqual(m.changes(),[]);assert.equal(m.hasChanges(),false);
 assert.deepEqual(records,[{id:'host-1',surface:'草地',reading:'くさち'}]);
});
