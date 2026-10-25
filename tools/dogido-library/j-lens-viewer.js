/* 保存済み内部表現からの候補語。生データや句本文は変更しない。 */
(function(root){
  'use strict';
  function selectLayer(data,phase,layer){
    if(data.schema!=='dogido.j_lens.v1'||data.status!=='exploratory_transfer')throw Error('対応するJ-lensの記録ではありません。');
    const position=data.positions?.find(row=>row.phase===phase),row=position?.layers?.[layer];
    if(!position||row?.layer!==layer||!Array.isArray(row.candidates)||row.candidates.length!==16)throw Error('この時点・層の候補は記録されていません。');
    if(!row.candidates.every(v=>Number.isSafeInteger(v.id)&&typeof v.piece==='string'&&Number.isFinite(v.score)))throw Error('候補の記録を確認できませんでした。');
    return {position,row};
  }
  function createReader(fetchReadout,csrf){
    const pending=new Map();
    return function read(base){
      if(pending.has(base))return pending.get(base);
      const promise=(async()=>{
        let response=await fetchReadout(`${base}/j-lens`,{cache:'no-store'});
        if(response.status===404)response=await fetchReadout(`${base}/j-lens`,{
          method:'POST',headers:{'X-Dogido-Library':csrf},cache:'no-store'
        });
        const data=await response.json();
        if(!response.ok)throw Error(data.detail||'候補語を読み出せませんでした。');
        return data;
      })();
      pending.set(base,promise);
      promise.catch(()=>pending.delete(base));
      return promise;
    };
  }
  function copyOriginal(piece){
    const letters=Array.from(piece).filter(c=>/\p{L}/u.test(c));
    return !letters.length||letters.some(c=>/[\u3040-\u30ff\uff66-\uff9f]/u.test(c))&&letters.every(c=>/[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f\u{20000}-\u{3134f}]/u.test(c))||piece.startsWith('<')&&piece.endsWith('>');
  }
  function applyTranslations(list,rows){
    for(const li of list.children){
      const row=rows.find(r=>String(r.id)===li.dataset.tokenId&&r.piece===li.dataset.piece);
      if(!row||typeof row.text!=='string')continue;
      const cell=li.querySelector('.j-lens-translation');cell.textContent=row.text;cell.title=row.text;
    }
  }
  const api={selectLayer,createReader,copyOriginal};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  let reader;
  const versions=new WeakMap();
  async function load(view){
    if(!reader)reader=createReader((...args)=>root.fetch(...args),root.DOGIDO_BOOTSTRAP?.csrf||'');
    const version={};versions.set(view,version);
    const pane=view.querySelector('.j-lens'),status=pane.querySelector('.j-lens-status'),list=pane.querySelector('.j-lens-candidates'),retry=pane.querySelector('.j-lens-retry'),translate=pane.querySelector('.j-lens-translate');
    translate.disabled=true;
    pane.querySelector('.j-lens-translation-status').textContent='';
    retry.hidden=true;list.replaceChildren();
    status.textContent='保存済みの数値から候補を読み出しています…（初回は数十秒）';
    try{
      const data=await reader(view.dataset.captureBase);
      if(!view.isConnected||versions.get(view)!==version)return;
      const layer=Number(pane.querySelector('select').value),{position,row}=selectLayer(data,pane.dataset.phase,layer);
      const download=view.closest('.capture-records>li')?.querySelector('.j-lens-download');
      if(download)download.hidden=false;
      status.textContent=`生成位置 ${position.position+1} / ${data.shape[0]} · 実際の出力 ${JSON.stringify(position.predicted_piece)}${layer===29?' · 最終層（J-lens変換なし）':''}`;
      for(const item of row.candidates){
        const li=document.createElement('li'),piece=document.createElement('code'),meaning=document.createElement('span'),score=document.createElement('small');
        piece.textContent=JSON.stringify(item.piece);score.textContent=item.score.toFixed(3);
        piece.title=piece.textContent;li.dataset.tokenId=String(item.id);li.dataset.piece=item.piece;
        meaning.className='j-lens-translation';meaning.textContent=copyOriginal(item.piece)?item.piece:'';meaning.title=meaning.textContent;
        li.title=`トークンID ${item.id} / score ${item.score} / logprob ${item.logprob}`;
        li.append(piece,meaning,score);list.append(li);
      }
      translate.disabled=false;
      const cached=await root.fetch(`${view.dataset.captureBase}/j-lens-translation?phase=${pane.dataset.phase}&layer=${layer}`,{cache:'no-store'});
      if(cached.ok){const translated=await cached.json();if(view.isConnected&&versions.get(view)===version)applyTranslations(list,translated.translations||[]);}
    }catch(error){
      if(view.isConnected&&versions.get(view)===version){status.textContent=error.message;retry.hidden=false;}
    }
  }
  async function translate(view){
    const version=versions.get(view),pane=view.querySelector('.j-lens'),button=pane.querySelector('.j-lens-translate'),status=pane.querySelector('.j-lens-translation-status');
    button.disabled=true;status.textContent='翻訳中…';
    try{
      const response=await root.fetch(`${view.dataset.captureBase}/j-lens-translation`,{method:'POST',headers:{'Content-Type':'application/json','X-Dogido-Library':root.DOGIDO_BOOTSTRAP?.csrf||''},
        body:JSON.stringify({phase:pane.dataset.phase,layer:Number(pane.querySelector('select').value)})});
      const value=await response.json();if(!response.ok)throw Error(value.detail||'翻訳できませんでした。');
      if(!view.isConnected||versions.get(view)!==version)return;
      applyTranslations(pane.querySelector('.j-lens-candidates'),value.translations);status.textContent='訳を表示しました。';
    }catch(error){if(view.isConnected&&versions.get(view)===version)status.textContent=error.message;}
    finally{if(view.isConnected&&versions.get(view)===version)button.disabled=false;}
  }
  document.addEventListener('toggle',event=>{
    if(event.target.matches('.capture-slice')&&event.target.open)load(event.target);
  },true);
  document.addEventListener('change',event=>{
    if(event.target.matches('.j-lens select'))load(event.target.closest('.capture-slice'));
  });
  document.addEventListener('click',event=>{
    if(event.target.closest('.j-lens-retry'))load(event.target.closest('.capture-slice'));
    if(event.target.closest('.j-lens-translate'))translate(event.target.closest('.capture-slice'));
  });
})(typeof window==='undefined'?globalThis:window);
