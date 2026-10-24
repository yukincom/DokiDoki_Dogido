/* 研究保存先の設定ページ。選択と確定を分け、正本の保存場所や旧保存先は変更しない。 */
(function(){
  'use strict';
  const boot=window.DOGIDO_BOOTSTRAP;if(!boot)return;
  const statusLabels={unconfigured:'未設定',ready:'保全済み',pending:'保全待ち',offline:'保存先に接続できません',error:'保存先を確認してください'};
  let storage=normalize(boot.photos?.researchStorage),selection=null,saving=false,picking=false,notice='',noticeKind='';
  const menu=document.createElement('button');menu.type='button';menu.className='catalog-settings-menu';menu.textContent='環境設定';
  menu.title='研究データの保存先を設定します';menu.addEventListener('click',open);
  function normalize(value){
    if(!value||typeof value!=='object')return {root:null,archiveId:null,state:'unconfigured',detail:'',lastSync:'',poems:null,captures:null};
    return {
      root:typeof value.root==='string'?value.root:null,
      archiveId:typeof value.archiveId==='string'?value.archiveId:null,
      state:Object.hasOwn(statusLabels,value.state)?value.state:'error',
      detail:typeof value.detail==='string'?value.detail:'',
      lastError:typeof value.lastError==='string'?value.lastError:'',
      displaySource:['archive','mixed','unavailable','local'].includes(value.displaySource)?value.displaySource:'',
      readError:typeof value.readError==='string'?value.readError:'',
      readPoems:Number.isSafeInteger(value.readPoems)?value.readPoems:null,
      lastSync:typeof value.lastSync==='string'?value.lastSync:'',
      poems:Number.isSafeInteger(value.poems)&&value.poems>=0?value.poems:null,
      captures:Number.isSafeInteger(value.captures)&&value.captures>=0?value.captures:null
    };
  }
  const isOpen=()=>state.book==='catalog'&&state.catalogPane==='settings'&&!document.body.classList.contains('library-photos');
  const bridge=()=>window.webkit?.messageHandlers?.dogidoResearchDirectory;
  const changed=()=>document.dispatchEvent(new Event('dogido-research-settings-changed'));
  function placeMenu(readingMenu=document.querySelector('.catalog-reading-menu')){
    if(state.book!=='catalog'||!readingMenu)return;
    readingMenu.after(menu);menu.setAttribute('aria-pressed',String(isOpen()));
  }
  function open(){
    closeBookCover();state.catalogPane='settings';state.listOpen=false;
    renderNavigation();renderDetail();
  }
  function renderPage(){
    if(!isOpen())return;
    const activeAction=document.activeElement?.dataset?.researchAction;
    const nativeAvailable=typeof bridge()?.postMessage==='function';
    const busy=saving||picking;
    const lastSync=storage.lastSync&&Number.isFinite(Date.parse(storage.lastSync))?new Date(storage.lastSync).toLocaleString('ja-JP'):'';
    const counts=[storage.poems===null?null:`句 ${storage.poems}件`,storage.captures===null?null:`写真 ${storage.captures}件`].filter(Boolean).join(' ・ ');
    $('detail').innerHTML=`<section class="research-settings" aria-labelledby="research-settings-title">
      <div class="entry-heading"><div><h2 id="research-settings-title">環境設定</h2><p class="lead">研究データの保存先</p></div></div>
      <p>句・対話・メモ・写真をまとめて残すフォルダを選びます。外付けSSDや、Macに接続済みのNASを指定できます。</p>
      <p>貼り付けを保存したF2画像は、コピーの検証後にここへ移動します。あんちょこの写真は <code>encyclopedia</code>、句集の写真は各句のフォルダに入ります。</p>
      <section class="research-storage-card" aria-labelledby="research-current-title">
        <div class="research-storage-heading"><h3 id="research-current-title">現在の保存先</h3><span class="research-storage-state" data-state="${storage.state}">${statusLabels[storage.state]}</span></div>
        <p class="research-storage-path">${esc(storage.root||'まだ設定されていません')}</p>
        ${storage.displaySource?`<p class="research-storage-reading">${storage.displaySource==='archive'?`閲覧元：選択した保存先（${storage.readPoems??0}句を照合済み）`:storage.displaySource==='mixed'?'閲覧元：選択した保存先 ＋ Mac内の保存待ちの変更':storage.displaySource==='unavailable'?'保存先の読み取りに失敗しています。読めない句はMac内の記録で補っています。画像は保存先の接続を確認してください。':'閲覧元：Mac内の記録'}</p>`:''}
        ${storage.detail?`<p class="research-storage-detail">${esc(storage.detail)}</p>`:''}
        ${storage.lastError?`<details class="research-storage-diagnostic"><summary>接続の確認内容</summary><p class="research-storage-path">${esc(storage.lastError)}</p></details>`:''}
        ${storage.readError?`<details class="research-storage-diagnostic"><summary>読み取りの確認内容</summary><p class="research-storage-path">${esc(storage.readError)}</p></details>`:''}
        ${lastSync||counts?`<p class="research-storage-summary">${lastSync?`最終同期：${esc(lastSync)}`:''}${lastSync&&counts?'<br>':''}${esc(counts)}</p>`:''}
      </section>
      ${selection?`<section class="research-storage-pending" aria-labelledby="research-pending-title"><h3 id="research-pending-title">選択したフォルダ <span>未保存</span></h3><p class="research-storage-path">${esc(selection.root)}</p><p>このフォルダを研究データの保存先として確定します。</p></section>`:''}
      <div class="research-settings-actions">
        <button type="button" data-research-action="choose" ${busy||!nativeAvailable?'disabled':''}>${picking?'フォルダを選択中…':selection?'別のフォルダを選ぶ':'保存先フォルダを選ぶ…'}</button>
        ${storage.root&&!selection?`<button type="button" data-research-action="reconnect" ${busy?'disabled':''}>この保存先へ接続し直す</button>`:''}
        ${selection?`<button type="button" data-research-action="save" class="research-save-button" ${busy?'disabled':''}>${saving?'保存中…':'このフォルダへ保存'}</button><button type="button" data-research-action="cancel" ${busy?'disabled':''}>選択を取り消す</button>`:''}
      </div>
      <p class="research-settings-notice" role="status" data-kind="${noticeKind}">${esc(notice)}</p>
      ${nativeAvailable?'':'<p class="research-settings-help">保存先の選択はMac版「ドギドの本棚」で利用できます。</p>'}
      <div class="research-settings-help"><p>保存する最後のフォルダそのものを選んでください。例：<code>/Volumes/mydata2025/dokidoki_dogido</code></p><p>その中に <code>年 / 月日時分</code>（例：<code>2026/10231430</code>）を自動で作ります。月・日・時・分は2桁で、秒や枝番は付けません。</p><p>保存済みの句と画像は選択した保存先から読みます。ゲームと編集に使うMac内の記録は残します。保存先を切り替えても、以前のフォルダ内のデータは移動・削除しません。</p></div>
    </section>`;
    if(activeAction)document.querySelector(`[data-research-action="${activeAction}"]`)?.focus({preventScroll:true});
  }
  function update(){placeMenu();renderPage();changed();}
  // 設定自身の未保存選択を除き、既存の句・写真・読み・メモの編集を先に保存してもらう。
  function canChange(){
    if(!window.dogidoLibraryHasUnsaved?.({ignoreResearchSettings:true}))return true;
    notice='保存していない編集があります。先に「変更を保存」を押してから、保存先を変更してください。';noticeKind='error';update();return false;
  }
  async function pickDirectory(){
    if(saving||picking||!canChange())return;
    const native=bridge();
    if(typeof native?.postMessage!=='function'){notice='保存先の選択にはMac版「ドギドの本棚」を使ってください。';noticeKind='error';update();return;}
    const expectedArchiveId=storage.archiveId;picking=true;notice='';noticeKind='';update();
    try{chooseDirectory(await native.postMessage('chooseResearchDirectory'),expectedArchiveId);}
    catch(error){notice=typeof error?.message==='string'?error.message:'保存先フォルダを選択できませんでした。';noticeKind='error';}
    finally{picking=false;update();}
  }
  // nativeのフォルダ選択結果だけを未保存選択へ変換する。ここではネットワーク保存をしない。
  function chooseDirectory(path,expectedArchiveId=storage.archiveId){
    if(!picking||saving)return;
    if(path===null||path===undefined)return;
    if(typeof path!=='string'||!path.startsWith('/')||path.includes('\0')||path.length>8192){notice='選択したフォルダを確認できませんでした。';noticeKind='error';return;}
    selection=path===storage.root&&expectedArchiveId===storage.archiveId?null:{root:path,expectedArchiveId};
    notice=selection?'フォルダを選びました。「このフォルダへ保存」で確定します。':'現在と同じ保存先です。';noticeKind='';
  }
  async function save(){
    if(saving||picking||!selection||!canChange())return;
    const submitted={...selection};saving=true;notice='';noticeKind='';update();
    try{
      const response=await fetch('/api/research-storage',{method:'PUT',headers:{'Content-Type':'application/json','X-Dogido-Library':boot.csrf},body:JSON.stringify(submitted)});
      const data=await response.json();
      if(!response.ok)throw Error((typeof data.detail==='string'?data.detail:'保存先を変更できませんでした。')+(typeof data.diagnostic==='string'?' '+data.diagnostic:''));
      if(!data.researchStorage||typeof data.researchStorage.root!=='string'||typeof data.researchStorage.archiveId!=='string'||!Object.hasOwn(statusLabels,data.researchStorage.state))throw Error('保存結果を確認できませんでした。保存先を確認してから再試行してください。');
      storage=normalize(data.researchStorage);(boot.photos??={}).researchStorage={...storage};selection=null;
      notice='研究データの保存先を保存しました。';noticeKind='success';
      document.dispatchEvent(new Event('dogido-research-storage-saved'));
    }catch(error){notice=typeof error?.message==='string'?error.message:'保存先を変更できませんでした。';noticeKind='error';}
    finally{saving=false;update();}
  }
  function cancel(){if(saving||picking)return;selection=null;notice='フォルダの選択を取り消しました。保存先は変更していません。';noticeKind='';update();}
  function refresh(value){
    // pollingや保存中の古い読込で、今回の選択や確定済みの返答を上書きしない。
    if(saving)return;
    storage=normalize(value);update();
  }
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-research-action]');if(!button||button.disabled||!isOpen())return;
    if(button.dataset.researchAction==='choose')void pickDirectory();
    if(button.dataset.researchAction==='save')void save();
    if(button.dataset.researchAction==='reconnect'&&!saving&&!picking&&storage.root&&canChange()){
      selection={root:storage.root,expectedArchiveId:storage.archiveId};void save();
    }
    if(button.dataset.researchAction==='cancel')cancel();
  });
  window.DogidoResearchSettings={isOpen,renderPage,placeMenu,chooseDirectory,refresh,hasChanges:()=>selection!==null,busy:()=>saving||picking};
  document.addEventListener('dogido-detail-rendered',()=>placeMenu());
  placeMenu();
})();
