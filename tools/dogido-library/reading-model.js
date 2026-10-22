(function(root){
  'use strict';
  function create(records=[]){
    const base=new Map(),draft=new Map();let sequence=0;
    for(const r of records){const key='saved-'+r.id;base.set(key,{...r});draft.set(key,{key,surface:r.surface,reading:r.reading});}
    const order=new Intl.Collator('ja');
    const rows=()=>[...draft.values()].map(r=>({...r,saved:base.has(r.key)})).sort((a,b)=>order.compare(a.reading,b.reading)||order.compare(a.surface,b.surface));
    function changes(){
      const edits=[];
      for(const [key,b] of base)if(!draft.has(key))edits.push({key,method:'DELETE',body:{surface:b.surface,expected_id:b.id}});
      for(const [key,d] of draft){const b=base.get(key);if(!b||d.reading!==b.reading)edits.push({key,method:'PUT',body:{surface:d.surface,reading:d.reading,expected_id:b?.id??null,entry_id:null,wrong_reading:b?.wrong_reading===d.reading?null:(b?.wrong_reading??null)}});}
      return edits;
    }
    return {
      rows,changes,hasChanges:()=>changes().length>0,
      add(surface,reading){
        surface=surface.trim();reading=reading.trim();
        if(!surface||surface.length>80||/[\u0000-\u001f]/.test(surface)||!(/^[ぁ-んー]{1,80}$/u.test(reading)))throw Error('言葉と、ひらがなの読みを入力してください。');
        if(rows().some(r=>r.surface===surface))throw Error('その言葉は登録済みです。既存の行を編集してください。');
        const key='new-'+(++sequence);draft.set(key,{key,surface,reading});return key;
      },
      edit(key,reading){const d=draft.get(key);if(d)d.reading=reading;},
      remove:key=>draft.delete(key),
      removeMany(keys){let count=0;for(const key of new Set(keys))if(draft.delete(key))count++;return count;},
      validate(){for(const r of rows())if(!/^[ぁ-んー]{1,80}$/u.test(r.reading))throw Error('読みはひらがなで入力してください。');},
      accept(edit,result){
        if(edit.method==='DELETE')base.delete(edit.key);
        else base.set(edit.key,{...result.correction});
      }
    };
  }
  const api={create};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DogidoReadingModel=api;
})(typeof window==='undefined'?globalThis:window);
