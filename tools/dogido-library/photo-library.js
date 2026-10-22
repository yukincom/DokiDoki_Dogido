/* Shared photo identities: one title, several page placements. Originals are never removed. */
(function(){
  'use strict';
  const boot=window.DOGIDO_BOOTSTRAP;
  const model=window.DogidoPhotoLibraryModel.create(boot?.photos);
  const page=$('photo-library'),picker=$('photo-picker');
  const activePages=new Map();
  let selected=null,picking=null,pickerSelected=null,pickerView='columns';
  const editable=!boot?.status.active;
  const disabled=()=>editable?'':'disabled';
  const changed=()=>document.dispatchEvent(new Event('dogido-photos-changed'));
  function photoFolderButton(){
    const available=Boolean(window.webkit?.messageHandlers?.dogidoPhotoFolder?.postMessage);
    return `<button type="button" class="photo-folder-button" data-show-photo-folder title="${available?'Minecraft標準のscreenshotsフォルダを開きます。写真の移動・取り込みはしません。':'Finder表示はMac版の本棚アプリで利用できます。'}" ${available?'':'disabled'}>写真の場所をFinderで表示</button>`;
  }
  function showPhotoFolder(){
    window.webkit?.messageHandlers?.dogidoPhotoFolder?.postMessage('showMinecraftScreenshots');
  }
  function pageTarget(){const entry=current();return entry?`${state.book}:${entry.id}`:null;}
  function titleInput(id){return `<input class="photo-title" data-photo-title="${esc(id)}" aria-label="写真のタイトル" title="タイトルを変更すると、貼り付け先にも反映します" maxlength="200" value="${esc(model.title(id))}" placeholder="${esc(model.find(id).filename.replace(/\.[^.]+$/,''))}" ${disabled()}>`;}
  function related(id){
    const targets=model.targets(id),poems=targets.filter(t=>t.startsWith('poems:')).map(t=>M.poems.find(p=>p.id===t.slice(6))).filter(Boolean);
    return poems.length?`<div class="photo-poems">${poems.map(p=>`<div class="photo-poem"><p>${p.lines.map(esc).join('<br>')}</p><button type="button" class="link-button" data-open-photo-poem="${esc(p.id)}" title="この句のページを開きます">句集へ →</button></div>`).join('')}</div>`:'';
  }
  function photoDetail(id,choose=false){
    const p=model.find(id);if(!p)return '';
    return `<img class="album-large-photo" src="${esc(p.url)}" alt=""><div class="album-photo-details">${choose?`<h3 data-photo-label="${esc(id)}">${esc(model.displayTitle(id))}</h3>`:titleInput(id)}${related(id)}${choose?'':`<button class="danger-button" type="button" data-delete-photo="${esc(id)}">この写真をごみ箱へ</button>`}</div>`;
  }
  function renderAlbum(){
    // 再描画で共用の表紙要素を消さない。
    const cover=$('book-cover');if(page.contains(cover))page.before(cover);
    const photos=model.all();if(!photos.some(p=>p.id===selected))selected=photos[0]?.id;
    page.innerHTML=`<div class="album-columns"><aside class="album-sidebar"><header class="album-heading"><h1>フォトブック</h1></header>${photoFolderButton()}<h2 class="album-list-heading" id="album-list-heading">写真の一覧</h2><nav class="album-list" aria-labelledby="album-list-heading">${photos.map(p=>`<button class="album-row" data-album-select="${esc(p.id)}" aria-pressed="${p.id===selected}"><img src="${esc(p.url)}" alt="" loading="lazy"><span data-photo-label="${esc(p.id)}">${esc(p.displayTitle)}</span></button>`).join('')}</nav></aside><article class="album-preview${photos.length?'':' is-empty'}" aria-label="選択した写真">${photos.length?photoDetail(selected):'<p class="album-empty">写真はまだありません。</p>'}</article></div>`;
    document.dispatchEvent(new Event('dogido-album-rendered'));
  }
  function updateTitleFields(id,source){
    document.querySelectorAll('[data-photo-title]').forEach(input=>{if(input.dataset.photoTitle===id&&input!==source)input.value=model.title(id);});
    document.querySelectorAll('[data-photo-label]').forEach(label=>{if(label.dataset.photoLabel===id)label.textContent=model.displayTitle(id);});
    document.querySelectorAll('[data-page-photo]').forEach(button=>{if(button.dataset.pagePhoto===id)button.setAttribute('aria-label',model.displayTitle(id));});
  }
  function renderPlaced(){
    renderPoemThumbnails();
    const target=pageTarget(),entry=current();if(!target||!entry)return;
    const detail=$('detail'),stage=detail.querySelector(state.book==='catalog'?'.illustration':'.poem-scene');if(!stage)return;
    detail.querySelectorAll('.page-photos,.placed-photo-title').forEach(el=>el.remove());
    stage.querySelector('.collected-photo')?.remove();
    stage.querySelectorAll('.art-caption,.specimen-mark').forEach(el=>el.hidden=true);
    const ids=model.slots(target),photos=ids.map(id=>id&&model.find(id));
    const active=model.mainPhoto(target,activePages.get(target))?.id;
    activePages.set(target,active);
    stage.classList.toggle('has-player-photo',Boolean(active));
    const illustration=stage.querySelector(':scope>svg');if(illustration)illustration.style.display=active||state.book==='poems'?'none':'';
    if(active){
      const img=document.createElement('img');img.className='collected-photo';img.src=model.find(active).url;img.alt='';stage.prepend(img);
    }
    const area=document.createElement('section');area.className='page-photos';area.setAttribute('aria-label','このページの写真');
    area.innerHTML=(active?`<div class="placed-photo-title">${titleInput(active)}</div>`:'')+`<div class="placed-photo-slots">${photos.map((p,i)=>p?`<div class="placed-photo-slot"><button class="placed-photo-select" data-page-photo="${esc(p.id)}" aria-label="${esc(model.displayTitle(p.id))}" aria-pressed="${active===p.id}"><img src="${esc(p.url)}" alt="" loading="lazy"></button><div class="placed-photo-actions"><button data-attach-slot="${i}" title="フォトブックから別の写真を選びます" ${disabled()}>貼り替え</button><button data-detach-slot="${i}" title="このページから外します。フォトブックの元画像は残ります" ${disabled()}>はがす</button></div></div>`:`<button class="paste-photo" data-attach-slot="${i}" aria-label="写真${i+1}を貼る" title="フォトブックからこの枠に貼る写真を選びます" ${disabled()}><span aria-hidden="true">＋</span>写真を貼る</button>`).join('')}</div>`;
    (state.book==='poems'?detail.querySelector('.poem-overview'):stage).after(area);
  }
  // 右頁と同じ選択済み写真を読むだけ。切抜きはCSSで行い、画像や保存状態は変更しない。
  function renderPoemThumbnails(){
    document.querySelectorAll('[data-poem-thumbnail]').forEach(thumb=>{
      const target=`poems:${thumb.dataset.poemThumbnail}`,photo=model.mainPhoto(target,activePages.get(target));
      thumb.replaceChildren();
      if(photo){const img=document.createElement('img');img.src=photo.url;img.alt='';img.loading='lazy';thumb.append(img);}
    });
  }
  function renderPicker(){
    const photos=model.all();if(!photos.some(p=>p.id===pickerSelected))pickerSelected=photos[0]?.id;
    const used=picking?model.slots(picking.target):[];
    picker.innerHTML=`<header class="picker-heading"><h2 id="photo-picker-title">写真を貼る</h2><button data-close-picker aria-label="閉じる" title="写真を選ばずに戻ります">×</button></header><nav class="picker-view" aria-label="写真の表示形式"><button data-picker-view="columns" aria-pressed="${pickerView==='columns'}" title="左のリストから写真を選びます">カラム</button><button data-picker-view="gallery" aria-pressed="${pickerView==='gallery'}" title="写真を並べて選びます">ギャラリー</button></nav><div class="picker-body ${pickerView}">${photos.length?`<div class="picker-list" aria-label="写真の選択">${photos.map(p=>{const other=used.some((id,i)=>id===p.id&&i!==picking.slot);return `<button data-picker-photo="${esc(p.id)}" aria-pressed="${p.id===pickerSelected}" ${other?'disabled title="このページに貼り付け済みです"':''}><img src="${esc(p.url)}" alt="" loading="lazy"><span>${esc(p.displayTitle)}</span></button>`;}).join('')}</div><article class="picker-preview">${photoDetail(pickerSelected,true)}</article>`:'<p class="album-empty">写真はまだありません。</p>'}</div><footer class="picker-footer"><button data-close-picker>キャンセル</button><button data-confirm-photo ${!pickerSelected||used.some((id,i)=>id===pickerSelected&&i!==picking.slot)?'disabled':''}>この写真を貼る</button></footer>`;
  }
  function openPicker(slot){
    picking={target:pageTarget(),slot,focus:document.activeElement};pickerSelected=model.slots(picking.target)[slot]||null;pickerView='columns';renderPicker();picker.showModal();
  }
  async function save(){
    if(!model.hasChanges())return;
    if(!boot)throw Error('この表示は試作です。保存には本棚アプリを使ってください。');
    const edits=model.changes();
    const response=await fetch('/api/photos',{method:'PUT',headers:{'Content-Type':'application/json','X-Dogido-Library':boot.csrf},body:JSON.stringify(edits)});
    const data=await response.json();if(!response.ok)throw Error(data.detail||'写真の変更を保存できませんでした。');
    model.accept(data,edits);boot.photos=data;
    for(const p of model.all())updateTitleFields(p.id);
  }
  window.DogidoPhotos={hasChanges:()=>model.hasChanges(),save,refresh(next){
    if(model.hasChanges()||picker.open)return false;
    if(!model.refresh(next))return false;
    boot.photos=next;renderAlbum();renderPlaced();placeBookCover();return true;
  }};
  document.addEventListener('dogido-album-opened',renderAlbum);
  document.addEventListener('dogido-detail-rendered',renderPlaced);
  document.addEventListener('input',event=>{
    const id=event.target.dataset.photoTitle;if(!id||!editable)return;
    if(model.rename(id,event.target.value)){updateTitleFields(id,event.target);changed();}
  });
  document.addEventListener('click',event=>{
    const button=event.target.closest('button');if(!button||button.disabled)return;
    if(button.hasAttribute('data-show-photo-folder')){showPhotoFolder();return;}
    if(button.dataset.deletePhoto){window.DogidoBookshelfDeletePhoto?.(button.dataset.deletePhoto,model.displayTitle(button.dataset.deletePhoto));return;}
    if(button.dataset.albumSelect){closeBookCover();selected=button.dataset.albumSelect;renderAlbum();page.querySelector(`[data-album-select="${selected}"]`)?.focus({preventScroll:true});}
    if(button.dataset.openPhotoPoem){switchBook('poems',button.dataset.openPhotoPoem);if(picker.open)picker.close();}
    if(button.dataset.attachSlot!==undefined&&editable)openPicker(Number(button.dataset.attachSlot));
    if(button.dataset.detachSlot!==undefined&&editable){model.detach(pageTarget(),Number(button.dataset.detachSlot));renderPlaced();changed();}
    if(button.dataset.pagePhoto){activePages.set(pageTarget(),button.dataset.pagePhoto);renderPlaced();}
    if(button.dataset.pickerView){pickerView=button.dataset.pickerView;renderPicker();}
    if(button.dataset.pickerPhoto){pickerSelected=button.dataset.pickerPhoto;renderPicker();}
    if(button.hasAttribute('data-close-picker'))picker.close();
    if(button.hasAttribute('data-confirm-photo')&&picking&&editable&&model.attach(picking.target,picking.slot,pickerSelected)){
      activePages.set(picking.target,pickerSelected);picker.close();renderPlaced();changed();
    }
  });
  picker.addEventListener('close',()=>{picking?.focus?.focus({preventScroll:true});picking=null;});
  renderAlbum();placeBookCover();renderPlaced();
})();
