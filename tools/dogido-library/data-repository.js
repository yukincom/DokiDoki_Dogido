/* 全句の未送信分を、メモ下の小さなアイコンからまとめて送る。 */
(function(){
  'use strict';
  const boot=window.DOGIDO_BOOTSTRAP;if(!boot?.dataRepository)return;
  const url=boot.dataRepository.url;if(url!=='https://github.com/yukincom/DokiDoki-Dogido-data')return;
  const uploadLabel='未送信のデータをまとめてアップロード';
  let busy=false,message='',result='';
  const github='<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path fill="currentColor" d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2.2c-3.3.7-4-1.4-4-1.4-.5-1.4-1.3-1.7-1.3-1.7-1.1-.8.1-.8.1-.8 1.2.1 1.8 1.2 1.8 1.2 1.1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-5.9 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.4 11.4 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.6 1.7.2 2.9.1 3.2.7.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6A12 12 0 0 0 12 .3Z"/></svg>';
  const upload='<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><g fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m7 8 5-5 5 5M7 13l5-5 5 5M12 8v10M5 18v3h14v-3"/></g></svg>';
  function render(){
    if(state.book!=='poems')return;
    const note=document.getElementById('player-note');if(!note||note.parentElement.querySelector('.data-repository'))return;
    const panel=document.createElement('div');panel.className='data-repository';
    panel.innerHTML=`<a class="data-repository-link" target="_blank" rel="noopener noreferrer" aria-label="GitHubでデータを見る" title="GitHubでデータを見る">${github}</a><button type="button" class="data-repository-upload" aria-label="${uploadLabel}">${upload}</button><span class="data-repository-status" role="status"></span>`;
    panel.querySelector('a').href=url;note.after(panel);update();
  }
  function update(){
    document.querySelectorAll('.data-repository-upload').forEach(b=>{b.disabled=busy;b.title=message||uploadLabel;b.dataset.result=result;b.setAttribute('aria-busy',String(busy));});
    document.querySelectorAll('.data-repository-status').forEach(p=>p.textContent=message);
  }
  document.addEventListener('click',async event=>{
    if(!event.target.closest('.data-repository-upload')||busy)return;
    if(window.dogidoLibraryHasUnsaved?.()){message='先に「変更を保存」を押してください。';result='error';update();return;}
    busy=true;result='';message='未送信のデータを確認しています…';update();
    try{
      const response=await fetch('/api/research-storage',{method:'POST',headers:{'X-Dogido-Library':boot.csrf}});
      const data=await response.json();if(!response.ok)throw Error(data.detail||'アップロードできませんでした。');
      message=data.status==='up_to_date'?'未送信のデータはありません。':'未送信のデータをアップロードしました。';result='success';
    }catch(error){message=error.message;result='error';}
    finally{busy=false;update();}
  });
  document.addEventListener('dogido-detail-rendered',render);render();
})();
