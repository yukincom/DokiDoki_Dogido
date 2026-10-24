(function(root){
  'use strict';
  const empty=()=>({revision:0,items:[],placements:{}});
  function create(snapshot=empty()){
    let base=snapshot;
    const titles=new Map(),placements=new Map();
    const find=id=>base.items.find(p=>p.id===id);
    const slots=target=>[...(placements.get(target)||base.placements[target]||[null,null,null])];
    const original=target=>find(base.originals?.[target])||null;
    const lockedSlot=(target,index)=>Boolean(original(target)&&slots(target)[index]===original(target).id);
    const title=id=>titles.has(id)?titles.get(id):find(id)?.title||'';
    const displayTitle=id=>title(id).trim()||(find(id)?.protected?'ドギドの視点':find(id)?.filename.replace(/\.[^.]+$/,'')||'');
    const all=()=>base.items.map(p=>({...p,title:title(p.id),displayTitle:displayTitle(p.id)}));
    const changes=()=>({revision:base.revision,titles:Object.fromEntries([...titles].filter(([id,t])=>t!==(find(id)?.title||''))),placements:Object.fromEntries([...placements].filter(([key,ids])=>JSON.stringify(ids)!==JSON.stringify(base.placements[key]||[null,null,null])))});
    return {
      all,slots,displayTitle,title,find,changes,original,lockedSlot,
      storageMessage(id,configured=true){
        const p=find(id);if(p?.source!=='minecraft_f2')return '';
        if(p.storageState==='stored')return 'F2画像 · SSDへ移動済み';
        if(p.storageState==='error')return 'F2画像 · 移動未完了。保存先と元画像を確認してください。';
        if(p.storageState==='pending'||p.storageState==='copied')return configured?'F2画像 · SSDへの移動待ち':'F2画像 · 保存先フォルダを設定してください。';
        return 'F2画像 · 貼り付けを「変更を保存」で確定するとSSDへ移動します。';
      },
      mainPhoto(target,preferred){const ids=slots(target),first=original(target);return ((ids.includes(preferred)||first?.id===preferred)&&find(preferred))||first||ids.map(find).find(Boolean)||null;},
      hasChanges(){const c=changes();return Object.keys(c.titles).length+Object.keys(c.placements).length>0;},
      refresh(next){
        if(this.hasChanges()||next.revision<base.revision)return false;
        titles.clear();placements.clear();base=next;return true;
      },
      rename(id,value){if(!find(id)||find(id).protected||typeof value!=='string'||value.length>200)return false;titles.set(id,value);return true;},
      attach(target,index,id){
        if(!/^(catalog|poems):.+/.test(target)||!Number.isInteger(index)||index<0||index>2||!find(id))return false;
        const ids=slots(target);if(lockedSlot(target,index)&&ids[index]!==id)return false;if(ids.some((p,i)=>p===id&&i!==index))return false;
        ids[index]=id;placements.set(target,ids);return true;
      },
      detach(target,index){if(!Number.isInteger(index)||index<0||index>2||lockedSlot(target,index))return false;const ids=slots(target);ids[index]=null;placements.set(target,ids);return true;},
      targets(id){return [...new Set([...Object.keys(base.placements),...placements.keys(),...Object.keys(base.originals||{})])].filter(t=>slots(t).includes(id)||original(t)?.id===id);},
      accept(next,sent={titles:{},placements:{}}){
        for(const [id,value] of Object.entries(sent.titles))if(titles.get(id)===value)titles.delete(id);
        for(const [target,value] of Object.entries(sent.placements))if(JSON.stringify(placements.get(target))===JSON.stringify(value))placements.delete(target);
        // Import and save responses may arrive out of order; never roll the snapshot back.
        if(next.revision>=base.revision)base=next;
      }
    };
  }
  const api={create,empty};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DogidoPhotoLibraryModel=api;
})(typeof window==='undefined'?globalThis:window);
