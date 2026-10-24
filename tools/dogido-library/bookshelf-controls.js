/* 本棚内の読み訂正と30日ごみ箱。ゲームUIへのリンクや操作は持たない。 */
(function(){
  'use strict';
  const boot=window.DOGIDO_BOOTSTRAP;if(!boot)return;
  const readings=window.DogidoReadingModel.create(boot.corrections);
  let busy=false,readingNotice='',pendingPoemIds=[];
  const selectedReadings=new Set(),addition={surface:'',reading:''};
  const readingPageOpen=()=>state.book==='catalog'&&state.catalogPane==='readings';
  const dialog=document.createElement('dialog');dialog.className='bookshelf-dialog';dialog.setAttribute('aria-labelledby','bookshelf-dialog-title');document.body.append(dialog);
  const restoreButton=document.createElement('button');restoreButton.type='button';restoreButton.className='library-restore-menu';restoreButton.setAttribute('aria-label','復元');restoreButton.title='復元';restoreButton.addEventListener('click',showTrash);
  restoreButton.innerHTML='<svg data-icon="restore-return" viewBox="0 0 28 28" width="24" height="24" aria-hidden="true" focusable="false"><path d="M10 7 2 15l8 8v-6h8a9 9 0 0 0 9-9V3h-4v5a5 5 0 0 1-5 5h-8Z"/></svg>';
  const allButton=document.createElement('button');allButton.textContent='読み訂正';allButton.className='text-button';allButton.addEventListener('click',showReadings);
  allButton.className='catalog-reading-menu';
  allButton.title='すべての読み訂正をひらきます';
  const deletePoemsButton=document.createElement('button');deletePoemsButton.type='button';deletePoemsButton.className='poem-delete-menu';deletePoemsButton.addEventListener('click',confirmSelectedPoems);
  deletePoemsButton.innerHTML='<svg viewBox="0 0 28 28" width="24" height="24" aria-hidden="true" focusable="false"><path d="M10 3h8v3h4l2 3H4l2-3h4Z"/><path fill-rule="evenodd" d="M6 11h16l-1.5 13h-13ZM10 14v7h2v-7Zm6 0v7h2v-7Z"/></svg><span class="poem-selection-count" aria-hidden="true"></span>';
  const poemActions=document.createElement('div');poemActions.className='poem-menu-actions';
  function placeMenuActions(){
    const head=indexPanel.querySelector('.index-head'),meta=indexPanel.querySelector('.result-meta');
    head.append(meta);poemActions.hidden=true;
    if(document.body.classList.contains('library-photos'))document.querySelector('.album-sidebar')?.append(restoreButton);
    else if(state.book==='poems'){poemActions.append(deletePoemsButton,restoreButton);head.append(poemActions);if(canSelectPoems()){poemActions.append(meta);poemActions.hidden=false;}updatePoemSelection();}
    else $('categories').append(restoreButton);
  }
  const originalNavigation=renderNavigation;
  const originalDetail=renderDetail;
  renderDetail=function(){
    if(window.DogidoResearchSettings?.isOpen())return window.DogidoResearchSettings.renderPage();
    return originalDetail();
  };
  renderNavigation=function(){
    originalNavigation();
    if(state.book==='catalog'){
      allButton.setAttribute('aria-pressed',String(readingPageOpen()));
      $('categories').append(allButton);
      window.DogidoResearchSettings?.placeMenu(allButton);
    }
    placeMenuActions();
  };
  if(state.book==='catalog')$('categories').append(allButton);
  placeMenuActions();
  document.addEventListener('dogido-album-rendered',placeMenuActions);
  const changed=()=>document.dispatchEvent(new Event('dogido-readings-changed'));
  function form(){
    return `<form data-reading-add><label>読み仮名<input name="reading" value="${esc(addition.reading)}" placeholder="例：くさち" maxlength="80" required></label><label>漢字・表記<input name="surface" value="${esc(addition.surface)}" placeholder="例：草地" maxlength="80" required></label><button type="submit">追加</button><p class="reading-error" role="status"></p></form>`;
  }
  function renderReadingsPage(){
    const rows=readings.rows();
    for(const key of selectedReadings)if(!rows.some(r=>r.key===key))selectedReadings.delete(key);
    $('detail').innerHTML=`<section class="readings-page" aria-labelledby="readings-title"><div class="entry-heading"><div><h2 id="readings-title">読み訂正</h2><p class="lead">読みの五十音順 <span aria-hidden="true">・</span> ${rows.length}件</p></div></div><section class="reading-add-section">${form()}</section><table class="reading-table"><thead><tr><th class="reading-check"><input type="checkbox" data-reading-select-all aria-label="すべての読み訂正を選択" ${rows.length?'':'disabled'}></th><th scope="col">読み</th><th scope="col">漢字・表記</th></tr></thead><tbody>${rows.map(r=>`<tr><td class="reading-check"><input type="checkbox" data-reading-select="${esc(r.key)}" aria-label="${esc(r.surface)}（${esc(r.reading)}）を選択" ${selectedReadings.has(r.key)?'checked':''}></td><td><input aria-label="${esc(r.surface)}の読み" data-reading-key="${esc(r.key)}" value="${esc(r.reading)}" maxlength="80"></td><td>${esc(r.surface)}</td></tr>`).join('')}</tbody></table>${rows.length?'':'<p class="reading-empty">登録された読み訂正はありません。</p>'}<p data-reading-page-status role="status">${esc(readingNotice)}</p><div class="reading-toolbar"><span data-reading-selection-count role="status"></span><button type="button" class="danger-button" data-remove-selected-readings disabled>選択した訂正を削除</button></div></section>`;
    updateSelection();
  }
  function updateSelection(){
    const page=document.querySelector('.readings-page');if(!page)return;
    const count=selectedReadings.size,total=readings.rows().length;
    page.querySelector('[data-reading-selection-count]').textContent=count?`${count}件を選択中`:'削除する項目にチェックをつけてください';
    page.querySelector('[data-remove-selected-readings]').disabled=count===0;
    const all=page.querySelector('[data-reading-select-all]');
    all.checked=total>0&&count===total;all.indeterminate=count>0&&count<total;
    page.querySelectorAll('[data-reading-select]').forEach(box=>box.checked=selectedReadings.has(box.dataset.readingSelect));
  }
  function decorate(){
    const old=document.querySelector('.catalog-reading'),entry=current();
    updatePoemSelection();
    if(old&&state.book==='catalog'){
      const surface=entry.raw.label;
      const matching=readings.rows().filter(r=>r.surface===surface);
      old.innerHTML=`<h3>読み訂正</h3>${matching.length?matching.map(r=>`<p>${esc(r.reading)} <span aria-hidden="true">｜</span> ${esc(r.surface)}</p>`).join(''):'<p>この言葉の読み訂正はまだありません。</p>'}<button type="button" data-show-readings>読み訂正の一覧を開く</button>`;
    }
    const notes=document.querySelector('.catalog-player-notes>p');
    if(notes)notes.textContent='この項目の個人メモです。読み訂正だけが次の読み上げに反映されます。';
    const label=document.querySelector('label[for="catalog-key-new"]');if(label)label.textContent='ことばのメモ';
    const foot=$('index-foot');if(foot)foot.textContent=state.book==='catalog'?'本体と共通のカタログ':'本体に保存された句';
  }
  function header(title){return `<header><h2 id="bookshelf-dialog-title">${esc(title)}</h2><button type="button" data-close-bookshelf aria-label="閉じる">×</button></header>`;}
  function updatePoemSelection(){
    const count=selectedPoems.ids().length;
    const label=count?`選択した${count}句を削除`:'選択した句を削除';
    deletePoemsButton.setAttribute('aria-label',label);deletePoemsButton.title=label;
    deletePoemsButton.querySelector('.poem-selection-count').textContent=count?String(count):'';
    deletePoemsButton.disabled=busy||!canSelectPoems()||count===0;
  }
  // 既存の一件ずつのごみ箱処理を順に呼ぶ。確認したIDだけを送り、部分成功を隠さない。
  function confirmSelectedPoems(){
    if(busy||!canSelectPoems()||!canReload())return;
    const poems=entries().filter(e=>selectedPoems.has(e.id));if(!poems.length)return;
    pendingPoemIds=poems.map(e=>e.id);
    dialog.dataset.mode='poems';dialog.innerHTML=header('選択した句をごみ箱へ移す')+`<ul class="poem-delete-preview">${poems.map(e=>`<li>${e.lines.map(esc).join('<br>')}</li>`).join('')}</ul><p>${poems.length}句を削除します。30日間は復元できます。貼り付けた写真は削除しません。</p><p data-dialog-status role="status"></p><div class="dialog-actions"><button type="button" data-close-bookshelf>キャンセル</button><button type="button" class="danger-button" data-confirm-poems>ごみ箱へ移す</button></div>`;
    if(!dialog.open)dialog.showModal();
  }
  async function deleteSelectedPoems(button){
    if(!canSelectPoems()||!canReload())return;
    // 確認画面を開いた後の選択変化や、別ページの項目を削除へ混ぜない。
    selectedPoems.retain(M.poems.filter(e=>pendingPoemIds.includes(e.id)));
    busy=true;button.disabled=true;updatePoemSelection();
    try{
      const result=await selectedPoems.trash(id=>request('/api/trash','PUT',{kind:'poem',id,restore:false}));
      for(const id of result.removed){const index=M.poems.findIndex(e=>e.id===id);if(index>=0)M.poems.splice(index,1);}
      pendingPoemIds=result.remaining;
      if(result.error){
        dialog.querySelector('[data-dialog-status]').textContent=`${result.removed.length}句をごみ箱へ移しました。残り${result.remaining.length}句は削除していません。${result.error.message}`;
        button.textContent='残りの句を再試行';
      }else dialog.close();
      renderList();
    }finally{busy=false;button.disabled=false;updatePoemSelection();}
  }
  function showReadings(){
    closeBookCover();
    state.catalogPane='readings';state.listOpen=false;
    renderNavigation();renderDetail();
  }
  function canReload(){
    if(!window.dogidoLibraryHasUnsaved?.())return true;
    dialog.dataset.mode='unsaved';dialog.innerHTML=header('保存していない変更があります')+'<p>編集内容を残すため、削除・再読み込みを止めました。先に「変更を保存」を押してください。</p><div class="dialog-actions"><button type="button" data-close-bookshelf>本棚に戻る</button></div>';
    if(!dialog.open)dialog.showModal();return false;
  }
  async function request(url,method,body){
    const response=await fetch(url,{method,headers:{'Content-Type':'application/json','X-Dogido-Library':boot.csrf},body:body===undefined?undefined:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw Error(data.detail||'操作できませんでした。');return data;
  }
  async function save(){
    if(!readings.hasChanges())return;
    readings.validate();
    for(const edit of readings.changes()){
      const data=await request('/api/readings',edit.method,edit.body);readings.accept(edit,data);
    }
    readingNotice='保存しました。次の読み上げから反映されます。';
    // 保存のたびに入力欄を破棄すると標準undoとカーソル位置が失われる。
    if(readingPageOpen())document.querySelector('[data-reading-page-status]').textContent=readingNotice;else decorate();
    changed();
  }
  window.DogidoReadings={hasChanges:()=>readings.hasChanges(),save,renderPage:renderReadingsPage,hasInput:()=>Boolean(addition.surface.trim()||addition.reading.trim()),busy:()=>busy};
  window.DogidoBookshelfDeletePhoto=(id,label)=>confirmDelete('photo',id,label);
  async function showTrash(){
    dialog.dataset.mode='trash';dialog.innerHTML=header('ごみ箱')+'<p>削除から30日間は復元できます。期限後は本棚の起動時・起動中に、句と直し履歴・写真の元画像を削除します。</p><p data-dialog-status role="status">読み込み中…</p><ul class="trash-list"></ul>';
    if(!dialog.open)dialog.showModal();
    // 閉じた画面・別の確認画面・新しい読込へ、古い応答を反映しない。
    const status=dialog.querySelector('[data-dialog-status]'),list=dialog.querySelector('.trash-list');
    const active=()=>dialog.open&&dialog.dataset.mode==='trash'&&status.isConnected;
    try{
      const data=await request('/api/trash','GET');if(!active())return;
      status.textContent=data.items.length?'':'ごみ箱は空です。';
      list.innerHTML=data.items.map(p=>`<li><div><strong>${p.kind==='poem'?'句':'写真'}：${esc(p.label)}</strong><small>復元期限：${esc(new Date(p.expiresAt).toLocaleString('ja-JP'))}</small></div><button type="button" data-restore-kind="${esc(p.kind)}" data-restore-id="${esc(p.id)}">復元</button></li>`).join('');
    }catch(e){if(active())status.textContent=e.message;}
  }
  function confirmDelete(kind,id,label){
    if(!canReload())return;
    dialog.dataset.mode='delete';dialog.innerHTML=header(kind==='poem'?'句をごみ箱へ移す':'写真をごみ箱へ移す')+`<p>${esc(label)}</p><p>30日間は復元できます。${kind==='poem'?'この句に貼った写真は削除しません。':'各ページからも非表示になります。復元すると、変更されていない貼り付け枠にも戻ります。'}</p><p data-dialog-status role="status"></p><div class="dialog-actions"><button type="button" data-close-bookshelf>キャンセル</button><button type="button" class="danger-button" data-confirm-kind="${kind}" data-confirm-id="${esc(id)}">ごみ箱へ移す</button></div>`;
    if(!dialog.open)dialog.showModal();
    if(kind==='photo'&&boot.photos.items.some(p=>p.id===id&&p.source==='minecraft')){
      const notice=document.createElement('p');
      notice.textContent='Minecraftの標準フォルダにある元画像は削除しません。30日後に消えるのは本棚の登録だけです。';
      dialog.querySelector('.dialog-actions').before(notice);
    }
  }
  document.addEventListener('submit',event=>{
    if(!event.target.matches('[data-reading-add]'))return;
    event.preventDefault();const f=event.target;
    try{readings.add(f.elements.surface.value,f.elements.reading.value);addition.surface='';addition.reading='';readingNotice='追加しました。「変更を保存」で確定します。';renderReadingsPage();changed();}
    catch(e){f.querySelector('.reading-error').textContent=e.message;}
  });
  document.addEventListener('input',event=>{
    if(event.target.dataset.readingKey){readings.edit(event.target.dataset.readingKey,event.target.value);readingNotice='';changed();}
    if(event.target.closest('[data-reading-add]')){addition[event.target.name]=event.target.value;}
  });
  document.addEventListener('change',event=>{
    const poemId=event.target.dataset.poemSelect;
    if(poemId&&state.book==='poems'&&!busy){
      if(!canSelectPoems())return;
      selectedPoems.set(poemId,event.target.checked);state.selected=poemId;renderList();
      [...document.querySelectorAll('[data-poem-select]')].find(box=>box.dataset.poemSelect===poemId)?.focus({preventScroll:true});
    }
    if(event.target.dataset.readingSelect){const key=event.target.dataset.readingSelect;event.target.checked?selectedReadings.add(key):selectedReadings.delete(key);updateSelection();}
    if(event.target.hasAttribute('data-reading-select-all')){selectedReadings.clear();if(event.target.checked)for(const row of readings.rows())selectedReadings.add(row.key);updateSelection();}
  });
  document.addEventListener('click',async event=>{
    const b=event.target.closest('button');if(!b||busy)return;
    if(b.hasAttribute('data-close-bookshelf'))dialog.close();
    if(b.hasAttribute('data-show-readings'))showReadings();
    if(b.hasAttribute('data-confirm-poems')){await deleteSelectedPoems(b);return;}
    if(b.hasAttribute('data-remove-selected-readings')){
      const count=readings.removeMany([...selectedReadings]);selectedReadings.clear();
      readingNotice=`${count}件を削除対象にしました。「変更を保存」で確定します。`;
      renderReadingsPage();changed();
    }
    const restore=b.dataset.restoreId,remove=b.dataset.confirmId;
    if(restore||remove){
      if(!canReload())return;
      busy=true;b.disabled=true;
      try{await request('/api/trash','PUT',{kind:restore?b.dataset.restoreKind:b.dataset.confirmKind,id:restore||remove,restore:Boolean(restore)});busy=false;location.reload();}
      catch(e){dialog.querySelector('[data-dialog-status]').textContent=e.message;}
      finally{busy=false;b.disabled=false;}
    }
  });
  dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  document.addEventListener('dogido-detail-rendered',decorate);decorate();
})();
