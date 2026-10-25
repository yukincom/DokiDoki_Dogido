/* 保存済みfloat32の読み取り専用表示。生成・要約・保存内容の変更はしない。 */
(function(root){
  'use strict';
  function coordinates(fields){
    const result={};
    for(const name of ['position','layer','offset']){
      const field=fields[name],value=Number(field.value),maximum=Number(field.max);
      if(!Number.isSafeInteger(value)||value<1||value>maximum)throw Error('表示位置を範囲内の整数で指定してください。');
      result[name]=value-1;
    }
    return result;
  }
  function formatValues(data){
    if(!Array.isArray(data.values)||data.values.length>64||!data.values.every(Number.isFinite))throw Error('数値の記録を確認できませんでした。');
    return data.values.map((value,index)=>`${data.offset+index+1}\t${Object.is(value,-0)?'-0':String(value)}`).join('\n');
  }
  function createLoader(fetchValues){
    const running=new WeakMap();
    return async function load(view){
      const previous=running.get(view);previous?.abort();
      const controller=new AbortController();running.set(view,controller);
      const status=view.querySelector('.capture-value-status'),output=view.querySelector('.capture-values');
      output.textContent='';
      try{
        const fields=Object.fromEntries(['position','layer','offset'].map(name=>[name,view.querySelector(`[name="${name}"]`)]));
        const query=coordinates(fields);
        for(const button of view.querySelectorAll('[data-capture-offset]')){
          const next=query.offset+Number(button.dataset.captureOffset);
          button.disabled=next<0||next>=Number(fields.offset.max);
        }
        status.textContent='記録された数値を読み込んでいます…';
        const response=await fetchValues(`${view.dataset.captureBase}/values?${new URLSearchParams(query)}`,{signal:controller.signal,cache:'no-store'});
        const data=await response.json();
        if(running.get(view)!==controller||!view.isConnected)return;
        if(!response.ok)throw Error(data.detail||'数値の記録を読み込めませんでした。');
        if(data.position!==query.position||data.layer!==query.layer||data.offset!==query.offset)throw Error('表示位置と数値の記録が一致しません。');
        output.textContent=formatValues(data);
        status.textContent=`トークンID ${data.predicted_token_id} · 入力位置 ${data.input_position} · 次元 ${data.offset+1}〜${data.offset+data.values.length} / ${data.shape[2]}`;
      }catch(error){
        if(running.get(view)===controller&&view.isConnected&&error.name!=='AbortError')status.textContent=error.message;
      }
    };
  }
  const api={coordinates,formatValues,createLoader};
  if(typeof module!=='undefined'&&module.exports){module.exports=api;return;}
  const load=createLoader((...args)=>root.fetch(...args));
  document.addEventListener('toggle',event=>{
    if(event.target.matches('.capture-slice')&&event.target.open)load(event.target);
  },true);
  document.addEventListener('change',event=>{
    const view=event.target.closest('.capture-slice');
    if(view&&event.target.matches('.capture-selectors input'))load(view);
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-capture-offset]');
    if(!button)return;
    const view=button.closest('.capture-slice'),field=view.querySelector('[name="offset"]');
    field.value=String(Number(field.value)+Number(button.dataset.captureOffset));load(view);
  });
})(typeof window==='undefined'?globalThis:window);
