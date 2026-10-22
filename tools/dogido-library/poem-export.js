/* 保存済みの句を月範囲で出力。未保存入力や現在の絞込みとは混ぜない。 */
(function(){
  'use strict';
  const boot=window.DOGIDO_BOOTSTRAP;if(!boot)return;
  const button=document.createElement('button');button.type='button';button.className='poem-export-button';button.textContent='JSONを書き出す';
  document.querySelector('.book-label').after(button);
  const dialog=document.createElement('dialog');dialog.className='poem-export-dialog';dialog.setAttribute('aria-labelledby','poem-export-title');
  dialog.innerHTML='<form><h2 id="poem-export-title">句と制作記録を書き出す</h2><p>発句した月（日本時間）で選びます。元句・編集後の句・修正履歴・対話・材料・メモ・写真参照を一つのJSONにまとめます。後日の修正や対話も含み、削除した句と写真の画像本体は含みません。</p><label>開始月<input name="startMonth" type="month" required min="2000-01" max="9998-12"></label><label>終了月<input name="endMonth" type="month" required min="2000-01" max="9998-12"></label><p class="export-notice" role="status"></p><footer><button type="button" data-cancel>閉じる</button><button type="submit">JSONを保存</button></footer></form>';
  document.body.append(dialog);
  const form=dialog.querySelector('form'),notice=dialog.querySelector('.export-notice'),submit=form.querySelector('[type=submit]');let busy=false;
  const month=value=>{const date=new Date(value);return Number.isNaN(date.valueOf())?'':new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit'}).format(date);};
  button.addEventListener('click',()=>{
    const months=M.poems.map(e=>month(e.createdAt)).filter(Boolean).sort(),current=month(new Date());
    form.elements.startMonth.value=months[0]||current;form.elements.endMonth.value=months.at(-1)||current;
    notice.textContent='現在の一覧の絞込みにかかわらず、指定月の保存済みの句を出力します。';dialog.showModal();
  });
  dialog.querySelector('[data-cancel]').addEventListener('click',()=>{if(!busy)dialog.close();});
  dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  form.addEventListener('submit',async event=>{
    event.preventDefault();if(busy)return;
    if(window.dogidoLibraryHasUnsaved?.()){notice.textContent='先に「変更を保存」を押してください。入力途中の内容は出力しません。';return;}
    const period={startMonth:form.elements.startMonth.value,endMonth:form.elements.endMonth.value};
    if(period.startMonth>period.endMonth){notice.textContent='終了月は開始月以降にしてください。';return;}
    busy=true;submit.disabled=true;notice.textContent='保存済みの記録を読み込んでいます…';
    try{
      const response=await fetch('/api/poems/export',{method:'POST',headers:{'Content-Type':'application/json','X-Dogido-Library':boot.csrf},body:JSON.stringify(period)});
      const data=await response.json();if(!response.ok)throw Error(data.detail||'書き出せませんでした。');
      const text=JSON.stringify(data,null,2)+'\n',native=window.webkit?.messageHandlers?.dogidoExport;
      if(native){const result=await native.postMessage(text);notice.textContent=result==='saved'?`${data.poems.length}句を書き出しました。`:'保存をキャンセルしました。';}
      else{
        const url=URL.createObjectURL(new Blob([text],{type:'application/json;charset=utf-8'})),link=document.createElement('a');
        link.href=url;link.download=`dogido-poems-${period.startMonth}-${period.endMonth}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
        notice.textContent=`${data.poems.length}句のダウンロードを開始しました。`;
      }
    }catch(error){notice.textContent=error.message||'書き出せませんでした。';}
    finally{busy=false;submit.disabled=false;}
  });
  const decorate=()=>{button.hidden=state.book!=='poems';};document.addEventListener('dogido-detail-rendered',decorate);decorate();
})();
