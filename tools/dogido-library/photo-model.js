(function(root){
  'use strict';
  // Existing preview screenshots, never a newly captured game frame.
  const samples=Object.freeze([
    Object.freeze({id:'sample-day',src:'../character-placement/day-full.png',label:'森の小道',provenance:'既存の昼のプレイ画像・撮影結果の代用品'}),
    Object.freeze({id:'sample-night',src:'../character-placement/night-full.png',label:'暗い場所',provenance:'既存の夜のプレイ画像・撮影結果の代用品'}),
    Object.freeze({id:'sample-third',src:'../character-placement/day-full.png',label:'3枚目の枠確認用',provenance:'昼画像を再利用した3枚目の代用品・別の撮影画像ではありません'})
  ]);
  function key(target){
    if(!target||!['catalog','poems'].includes(target.book)||typeof target.id!=='string'||!target.id||!['catalog','landscape','player'].includes(target.view))throw new Error('Invalid photo target');
    if((target.book==='catalog')!==(target.view==='catalog'))throw new Error('Invalid photo view');
    return JSON.stringify([target.book,target.id,target.view]);
  }
  function createAlbum(){
    const links=new Map(),active=new Map();
    // Three photos per catalog entry / poem, shared across framing choices.
    function entryKey(target){key(target);return JSON.stringify([target.book,target.id]);}
    return {
      get(target){const k=entryKey(target),list=links.get(k)||[];return list.find(p=>p.id===active.get(k))||list[0]||null;},
      list(target){return [...(links.get(entryKey(target))||[])];},
      attach(target,id){const sample=samples.find(p=>p.id===id);if(!sample)throw new Error('Unknown sample');const photo=Object.freeze({...sample,view:target.view});const k=entryKey(target),list=links.get(k)||[];if(!list.some(p=>p.id===id)){if(list.length>=3)throw new Error('Album full');links.set(k,[...list,photo]);}active.set(k,id);return photo;},
      select(target,id){const k=entryKey(target);if(!(links.get(k)||[]).some(p=>p.id===id))throw new Error('Photo not in album');active.set(k,id);},
      remove(target,id){const k=entryKey(target);links.set(k,(links.get(k)||[]).filter(p=>p.id!==id));if(active.get(k)===id)active.delete(k);},
      clear(target){const k=entryKey(target);links.delete(k);active.delete(k);}
    };
  }
  const api={samples,key,createAlbum};
  if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DogidoPhotoModel=api;
})(typeof window==='undefined'?globalThis:window);
