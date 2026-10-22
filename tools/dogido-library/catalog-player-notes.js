(function(root){
  'use strict';
  const LIMIT=10, PREFIX='dogido-library:catalog-player-notes:v1:';
  function create(entries,storage){
    const allowed=new Set(entries.map(e=>`${e.category}:${e.id}`)),cache=new Map(),drafts=new Map();
    function record(key){
      if(!allowed.has(key))return null;
      if(!cache.has(key)){
        let data={keys:[],readings:[]},saved=true;
        try{
          const raw=storage.getItem(PREFIX+key);
          if(raw!==null){
            const value=JSON.parse(raw);
            if(!value||!Array.isArray(value.keys)||value.keys.length>LIMIT||value.keys.some(k=>typeof k!=='string'))throw new Error('Invalid notes');
            // Preserve the old single reading without guessing its target word.
            const readings=value.readings??(typeof value.reading==='string'?(value.reading?[{word:'',reading:value.reading}]:[]):null);
            if(!Array.isArray(readings)||readings.some(r=>!r||typeof r.word!=='string'||typeof r.reading!=='string'))throw new Error('Invalid readings');
            data={keys:[...value.keys],readings:readings.map(r=>({word:r.word,reading:r.reading}))};
          }
        }catch{saved=false;}
        cache.set(key,{...data,saved});
      }
      return cache.get(key);
    }
    function persist(key,data){
      try{storage.setItem(PREFIX+key,JSON.stringify({keys:data.keys,readings:data.readings}));data.saved=true;}
      catch{data.saved=false;}
      return true;
    }
    return {
      // 保存済みsnapshotの更新後、次の読取を新しいstorageへ向ける。未追加の入力は保持する。
      reload(){cache.clear();},
      // 未追加の入力も項目ごとに保持する。追加が成功するまで保存済みメモには混ぜない。
      getDraft(key){return drafts.get(key)||'';},
      setDraft(key,value){if(!allowed.has(key)||typeof value!=='string')return false;if(value)drafts.set(key,value);else drafts.delete(key);return true;},
      hasDrafts(){return [...drafts.values()].some(value=>value.trim());},
      get(key){const data=record(key);return data?{...data,keys:[...data.keys],readings:data.readings.map(r=>({...r}))}:null;},
      add(key,value){const data=record(key);if(!data||typeof value!=='string'||!value.trim()||data.keys.length>=LIMIT)return false;data.keys.push(value.trim());drafts.delete(key);return persist(key,data);},
      edit(key,index,value){const data=record(key);if(!data||!Number.isInteger(index)||index<0||index>=data.keys.length||typeof value!=='string')return false;data.keys[index]=value;return persist(key,data);},
      remove(key,index){const data=record(key);if(!data||!Number.isInteger(index)||index<0||index>=data.keys.length)return false;data.keys.splice(index,1);return persist(key,data);},
      addReading(key,word,reading){const data=record(key);if(!data||typeof word!=='string'||typeof reading!=='string'||!word.trim()||!reading.trim())return false;data.readings.push({word:word.trim(),reading:reading.trim()});return persist(key,data);},
      editReading(key,index,field,value){const data=record(key);if(!data||!Number.isInteger(index)||index<0||index>=data.readings.length||!['word','reading'].includes(field)||typeof value!=='string')return false;data.readings[index][field]=value;return persist(key,data);},
      removeReading(key,index){const data=record(key);if(!data||!Number.isInteger(index)||index<0||index>=data.readings.length)return false;data.readings.splice(index,1);return persist(key,data);}
    };
  }
  const api={create,LIMIT,PREFIX};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DogidoCatalogPlayerNotes=api;
})(typeof window==='undefined'?globalThis:window);
