/* 明示操作だけで句集・候補・生データを非公開リポジトリへまとめて保存する。 */
(function(){
  'use strict';
  const boot=window.DOGIDO_BOOTSTRAP;if(!boot?.dataRepository)return;
  const url=boot.dataRepository.url;if(url!=='https://github.com/yukincom/DokiDoki-Dogido-data')return;
  let busy=false,message='';
  function render(){
    if(state.book!=='poems')return;
    const record=document.querySelector('.poem-record');if(!record||record.querySelector('.data-repository'))return;
    const panel=document.createElement('details');panel.className='data-repository';
    panel.innerHTML='<summary>非公開リポジトリ</summary><p>保存済みの句集・J-lens候補・生データをまとめてアップロードします。写真は参照情報のみです。</p><p><a target="_blank" rel="noopener noreferrer">GitHubでデータを見る ↗</a></p><button type="button" class="data-repository-upload">まとめてアップロード</button><p class="data-repository-status" role="status"></p>';
    panel.querySelector('a').href=url;panel.querySelector('button').disabled=busy;panel.querySelector('[role=status]').textContent=message;record.append(panel);
  }
  function update(){document.querySelectorAll('.data-repository-upload').forEach(b=>b.disabled=busy);document.querySelectorAll('.data-repository-status').forEach(p=>p.textContent=message);}
  document.addEventListener('click',async event=>{
    if(!event.target.closest('.data-repository-upload')||busy)return;
    if(window.dogidoLibraryHasUnsaved?.()){message='先に「変更を保存」を押してください。';update();return;}
    busy=true;message='保存済みの記録を照合し、候補を読み出してアップロードしています…';update();
    try{
      const response=await fetch('/api/research-storage',{method:'POST',headers:{'X-Dogido-Library':boot.csrf}});
      const data=await response.json();if(!response.ok)throw Error(data.detail||'アップロードできませんでした。');
      message=`${data.poems}句・採取${data.captures}件をアップロードし、GitHubからの再取得も確認しました。`;
    }catch(error){message=error.message;}
    finally{busy=false;update();}
  });
  document.addEventListener('dogido-detail-rendered',render);render();
})();
