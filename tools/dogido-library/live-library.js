/* 本棚の入力をまとめて保存する。句は既存revision、個人メモと写真は各保存先へ。 */
(function(){
  'use strict';
  const boot=window.DOGIDO_BOOTSTRAP;if(!boot)return;
  let revision=boot.player.revision,saving=false;
  const bar=document.createElement('div');bar.className='library-save-control';bar.tabIndex=0;bar.setAttribute('aria-label','保存の説明');bar.setAttribute('aria-describedby','library-save-help');
  const banner=document.createElement('p');banner.id='library-save-help';banner.className='library-save-tooltip';banner.setAttribute('role','status');
  const saveButton=document.createElement('button');saveButton.id='library-save';saveButton.className='export';saveButton.textContent='変更を保存';
  saveButton.setAttribute('aria-describedby',banner.id);
  bar.append(saveButton,banner);document.querySelector('.header-actions').append(bar);
  const label='読み訂正は次の読み上げから反映されます。個人メモは本棚に保存します。';
  function saveStatus(text,notice=false){banner.textContent=text;bar.classList.toggle('has-notice',notice);bar.classList.remove('help-dismissed');}
  bar.addEventListener('keydown',event=>{if(event.key==='Escape'){bar.classList.remove('has-notice');bar.classList.add('help-dismissed');}});
  bar.addEventListener('pointerleave',()=>bar.classList.remove('help-dismissed'));
  bar.addEventListener('focusout',()=>bar.classList.remove('help-dismissed'));
  function changes(){
    const found=[];
    for(const [key,json] of liveDrafts){
      const value=JSON.parse(json),old=boot.player.catalog[key]||{keys:[],readings:[]};
      if(JSON.stringify(value)!==JSON.stringify(old))found.push({section:'catalog',key,value});
    }
    for(const poem of M.poems){
      const key=poem.id,value={favorite:state.favorites.has(key),note:playerNotes.get(key)};
      const old=boot.player.poems[key]||{favorite:false,note:''};
      if(JSON.stringify(value)!==JSON.stringify(old))found.push({section:'poems',key,value});
    }
    return found;
  }
  function unadded(){return catalogNotes.hasDrafts()||['catalog-reading-word','catalog-reading-new'].some(id=>$(id)?.value.trim());}
  const photoDirty=()=>Boolean(window.DogidoPhotos?.hasChanges());
  const readingDirty=()=>Boolean(window.DogidoReadings?.hasChanges());
  const poemDirty=()=>Boolean(window.DogidoPoemEditor?.hasChanges());
  window.dogidoLibraryHasUnsaved=(options={})=>saving||changes().length>0||Boolean(unadded())||photoDirty()||readingDirty()||poemDirty()||Boolean(window.DogidoReadings?.hasInput())||Boolean(window.DogidoReadings?.busy())||(!options.ignoreResearchSettings&&(Boolean(window.DogidoResearchSettings?.hasChanges())||Boolean(window.DogidoResearchSettings?.busy())));
  function dirtyStatus(){
    if(saving)return;
    const dirty=changes().length>0||photoDirty()||readingDirty()||poemDirty();
    saveStatus(boot.status.active?label:dirty?'保存していない変更があります。'+label:label);
    saveButton.disabled=boot.status.active||!dirty;
    saveButton.classList.toggle('has-changes',dirty);
  }
  // Save all edited pages, not only the currently selected entry.
  async function saveAll(){
    if(saving||boot.status.active)return;
    if(unadded()||window.DogidoReadings?.hasInput()){saveStatus('入力中の言葉は「追加」を押してから保存してください。',true);return;}
    const edits=changes();
    if(edits.some(e=>e.section==='catalog'&&(e.value.keys.some(k=>!k.trim())||e.value.readings.some(r=>!r.word.trim()||!r.reading.trim())))){
      saveStatus('空欄の項目があります。言葉や読みを入力するか、その行を削除してください。',true);return;
    }
    saving=true;saveButton.disabled=true;saveStatus('保存しています…',true);
    try{
      for(const edit of edits){
        const response=await fetch('/api/library',{method:'PUT',headers:{'Content-Type':'application/json','X-Dogido-Library':boot.csrf},body:JSON.stringify({revision,...edit})});
        const data=await response.json();
        if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'保存できませんでした。入力内容を確認してください。');
        revision=data.revision;boot.player=data;
      }
      await window.DogidoPhotos?.save();
      await window.DogidoReadings?.save();
      await window.DogidoPoemEditor?.save();
      saveStatus(changes().length||photoDirty()||readingDirty()||poemDirty()?'保存中に編集した変更が残っています。もう一度保存してください。':'保存しました。',true);
    }catch(error){saveStatus(error.message,true);}
    finally{saving=false;const dirty=changes().length>0||photoDirty()||readingDirty()||poemDirty();saveButton.disabled=boot.status.active||!dirty;saveButton.classList.toggle('has-changes',dirty);}
  }
  let composing=false,saveAfterComposition=false;
  document.addEventListener('compositionstart',()=>{composing=true;});
  document.addEventListener('compositionend',()=>{composing=false;if(saveAfterComposition){saveAfterComposition=false;setTimeout(saveAll,0);}});
  window.dogidoLibrarySave=()=>{if(composing){saveAfterComposition=true;return;}return saveAll();};
  saveButton.addEventListener('click',window.dogidoLibrarySave);
  document.addEventListener('keydown',event=>{
    if((event.metaKey||event.ctrlKey)&&!event.altKey&&!event.shiftKey&&event.key.toLowerCase()==='s'){
      event.preventDefault();window.dogidoLibrarySave();
    }
    // ⌘Z／⇧⌘Z、切り取り・コピー・ペーストは入力欄の標準動作へ任せる。
  });
  window.addEventListener('beforeunload',event=>{if(window.dogidoLibraryHasUnsaved()){event.preventDefault();event.returnValue='';}});
  document.addEventListener('dogido-photos-changed',dirtyStatus);
  document.addEventListener('dogido-readings-changed',dirtyStatus);
  document.addEventListener('dogido-poems-changed',dirtyStatus);
  for(const name of ['click','input','submit'])document.addEventListener(name,event=>{
    if(event.target.closest('#library-save'))return;
    queueMicrotask(()=>{
      dirtyStatus();
    });
  });
  const months=[...new Set(M.poems.map(e=>e.date.slice(0,7)).filter(Boolean))].sort().reverse();
  $('period').innerHTML='<option value="all">すべて</option>'+months.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('');
  document.querySelector('footer').hidden=true;
  document.querySelector('.companion small').hidden=true;
  function decorate(){
    const e=current();
    $('result-count').textContent=state.book==='catalog'?`${entries().length} 項目`:`${entries().length} 句`;
    if(!e){dirtyStatus();return;}
    document.querySelectorAll('.page-footer span').forEach(el=>el.textContent=state.book==='catalog'?'あんちょこ':'句集');
    const storage=$('catalog-storage-status');if(storage)storage.textContent='編集後は「変更を保存」を押してください。';
    document.querySelector('[data-source="related"]')?.setAttribute('hidden','');
    if(state.book==='poems'){
      document.querySelectorAll('[data-related-catalog],.scene-tools').forEach(el=>el.hidden=true);
      document.querySelector('.art-caption').textContent='まだ写真がありません';
      document.querySelector('.poem-scene>svg')?.setAttribute('hidden','');
    }
    if(boot.status.active)document.querySelectorAll('.catalog-player-notes input,.catalog-player-notes button,#player-note,#favorite').forEach(el=>el.disabled=true);
    dirtyStatus();
  }
  document.addEventListener('dogido-detail-rendered',decorate);decorate();
  // 表示中の新しい句・写真だけ追従する。入力・保存・選択ダイアログ中は前後とも保留する。
  let refreshing=false,signature='';
  const canRefresh=()=>!document.hidden&&!window.dogidoLibraryHasUnsaved()
    &&!window.DogidoPoemEditor?.isOpen()
    &&!document.querySelector('dialog[open]')
    &&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement?.tagName)
    &&!document.activeElement?.isContentEditable;
  async function refresh(){
    if(refreshing||!canRefresh())return;
    refreshing=true;
    try{
      const response=await fetch('/api/library',{headers:{'X-Dogido-Library':boot.csrf}});
      if(!response.ok)return;
      const next=await response.json();
      if(!canRefresh()||next.player.revision<revision||next.photos.revision<boot.photos.revision)return;
      const nextSignature=JSON.stringify(next);if(nextSignature===signature)return;
      if(!window.DogidoPhotos?.refresh(next.photos))return;
      window.DogidoResearchSettings?.refresh(next.photos.researchStorage);
      M.poems.splice(0,M.poems.length,...next.poems.map(p=>({...p,biome:M.biomes[p.biomeId]})));
      boot.poems=M.poems;boot.player=next.player;revision=next.player.revision;
      state.favorites.clear();
      for(const p of M.poems){
        const note=next.player.poems[p.id]||{};
        playerNotes.set(p.id,note.note||'');if(note.favorite)state.favorites.add(p.id);
      }
      liveDrafts.clear();for(const [key,value] of Object.entries(next.player.catalog))liveDrafts.set(key,JSON.stringify(value));
      catalogNotes.reload();
      const months=[...new Set(M.poems.map(p=>p.date.slice(0,7)).filter(Boolean))].sort().reverse();
      if(state.period!=='all'&&!months.includes(state.period))state.period='all';
      $('period').innerHTML='<option value="all">すべて</option>'+months.map(m=>`<option value="${esc(m)}">${esc(m)}</option>`).join('');
      $('period').value=state.period;
      signature=nextSignature;
      renderNavigation();renderList();placeBookCover();dirtyStatus();
    }catch(error){/* 一時的な読取失敗では現在表示と入力を保持し、次回に再試行する。 */}
    finally{refreshing=false;}
  }
  setInterval(refresh,3000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
})();
