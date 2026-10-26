/* 本体の本棚データを表示し、メモと句の変更を画面内に保持する。保存はlive-libraryの既存HTTP処理が所有する。 */
'use strict';
const M=window.DogidoLibrary, catalog=window.DOGIDO_BOOTSTRAP.catalog;
const $=id=>document.getElementById(id);
const defaultCatalogEntry=catalog.find(e=>e.category==='mob'&&(e.catalogId||e.id)==='creeper')?.id??catalog[0]?.id??null;
const state={book:'catalog',catalogPane:'entry',category:'all',selected:defaultCatalogEntry,listOpen:false,query:'',biome:'all',timeOfDay:'all',period:'all',poemSection:'all',favorites:new Set(),source:'base',photo:'landscape'};
const playerNotes=M.createPlayerNotes(M.poems);
const selectedPoems=M.createPoemSelection(M.poems);
const liveDrafts=new Map(Object.entries(window.DOGIDO_BOOTSTRAP.player.catalog).map(([key,value])=>[key,JSON.stringify(value)]));
const catalogNotes=window.DogidoCatalogPlayerNotes.create(catalog.map(e=>e.catalogId?{...e,id:e.catalogId}:e),{
  getItem:key=>liveDrafts.get(key.slice(window.DogidoCatalogPlayerNotes.PREFIX.length))??null,
  setItem:(key,value)=>liveDrafts.set(key.slice(window.DogidoCatalogPlayerNotes.PREFIX.length),value)
});
for(const [id,value] of Object.entries(window.DOGIDO_BOOTSTRAP.player.poems)){
  playerNotes.set(id,value.note);if(value.favorite)state.favorites.add(id);
}
const catalogNoteId=e=>e.catalogId?e.id:`${e.category}:${e.id}`;
function catalogStorageMessage(){return '編集後は「変更を保存」を押してください。';}
function renderCatalogNotes(e){
  const data=catalogNotes.get(catalogNoteId(e)),full=data.keys.length>=10;
  return `<section class="catalog-player-notes" aria-label="個人メモ"><p>この項目の個人メモです。読み訂正だけが次の読み上げに反映されます。</p><form id="catalog-key-form"><label for="catalog-key-new">ことばのメモ</label><div class="catalog-key-row"><input id="catalog-key-new" value="${esc(catalogNotes.getDraft(catalogNoteId(e)))}" aria-describedby="catalog-key-help" ${full?'disabled':''}><button type="submit" ${full?'disabled':''}>追加</button></div></form><ul class="catalog-key-list">${data.keys.map((key,i)=>`<li class="catalog-key-row"><input aria-label="ことばのメモ ${i+1}" data-edit-key="${i}" value="${esc(key)}"><button type="button" data-delete-key="${i}" aria-label="ことばのメモ ${i+1} を削除">削除</button></li>`).join('')}</ul><p class="key-help" id="catalog-key-help">10個まで追加できます（${data.keys.length} / 10）</p>
  <section class="catalog-reading" aria-label="読み訂正"></section>
  <p class="storage-status" id="catalog-storage-status" role="status">${esc(catalogStorageMessage(e))}</p><p id="catalog-key-feedback" role="status"></p></section>`;
}
const indexPanel=document.querySelector('.index-page');
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const glyphs={mob:'生',biome:'地',block:'塊',item:'具',structure:'建'};
const poemSections={all:'すべての句',favorites:'しおりの句'};
$('biome').parentElement.firstChild.textContent='バイオーム';
$('biome').innerHTML='<option value="all">すべて</option>'+Object.entries(M.biomes).map(([id,label])=>`<option value="${esc(id)}">${esc(label)}</option>`).join('');
$('filters').insertAdjacentHTML('beforeend',`<label>ゲーム内の時間<select id="timeOfDay"><option value="all">すべて</option>${Object.entries(M.times).map(([id,label])=>`<option value="${id}">${label}</option>`).join('')}</select></label>`);
function poemTags(e,compact=false){
  return `<div class="poem-tags ${compact?'compact':''}" aria-label="句のタグ"><span class="biome-tag" title="${esc(e.biomeId)}">${esc(M.biomes[e.biomeId]||'バイオーム未記録')}<span class="biome-separator"> ： </span><span lang="en">${esc(M.biomeEnglish[e.biomeId]||'Unknown')}</span></span><span class="time-tag">${esc(M.times[e.timeOfDay]||'時間未記録')}</span></div>`;
}
function icon(entry){
  if(entry.catalogId)entry={...entry,id:entry.catalogId};
  const start='<svg viewBox="0 0 160 140" aria-hidden="true" shape-rendering="crispEdges">';
  const end='</svg>';
  if(entry.id==='sheep')return start+'<path fill="#b7bba0" d="M19 119h118v8H19z"/><path fill="#7c7260" d="M43 87h15v33H43zM102 88h16v32h-16z"/><path fill="#d8d1b8" d="M33 40h86v55H33z"/><path fill="#fff5d9" d="M25 43h74v53H25zM33 34h58v10H33zM33 95h58v8H33z"/><path fill="#e9dfc3" d="M99 43h22v52H99z"/><path fill="#b8a78d" d="M105 48h37v37h-37z"/><path fill="#e5d8bb" d="M100 40h43v17h-43zM97 47h10v27H97z"/><path fill="#383e30" d="M112 61h6v7h-6zM133 61h6v7h-6z"/><path fill="#dcaaa0" d="M117 76h14v9h-14z"/>'+end;
  if(entry.id==='creeper')return start+'<path fill="#6f8550" d="M54 22h54v47H54zM62 69h38v38H62zM47 106h25v21H47zM91 106h25v21H91z"/><path fill="#a0b975" d="M54 22h40v40H54zM62 69h25v34H62z"/><path fill="#303c29" d="M62 37h10v10H62zM86 37h10v10H86zM72 46h14v20H72zM67 53h24v9H67z"/>'+end;
  if(entry.id==='allay')return start+'<path fill="#c1ded4" d="M30 67h31v35H30zM97 67h31v35H97z"/><path fill="#72a9b8" d="M58 27h45v43H58zM66 70h29v34H66zM72 104h17v19H72z"/><path fill="#a4d6dc" d="M58 27h35v33H58z"/><path fill="#344d60" d="M64 45h7v8h-7zM86 45h7v8h-7z"/>'+end;
  if(entry.category==='biome')return start+'<path fill="#adc0ab" d="M17 36h125v66H17z"/><path fill="#788e57" d="M17 79h125v25H17z"/><path fill="#a0875d" d="M17 104h125v22H17z"/><path fill="#715a40" d="M91 46h9v42h-9z"/><path fill="'+(entry.id==='cherry_grove'?'#ce9c9e':'#4d7047')+'" d="M70 28h48v27H70zM62 40h65v21H62z"/><path fill="'+(entry.id==='river'?'#7fabb7':'#a3b673')+'" d="M17 96h65v8H17zM32 87h40v9H32z"/>'+end;
  if(entry.category==='structure')return start+'<path fill="#999678" d="M27 66h104v55H27z"/><path fill="#c4b287" d="M38 58h84v59H38z"/><path fill="#725e44" d="M20 55h120v15H20zM32 42h96v13H32zM47 29h67v13H47z"/><path fill="#615945" d="M71 88h19v29H71zM46 78h14v14H46zM101 78h14v14h-14z"/>'+end;
  if(entry.id==='iron_pickaxe')return start+'<path fill="#806548" d="M48 98h13v15H48zM59 86h13v15H59zM70 74h13v15H70zM81 61h13v15H81zM92 50h13v15H92z"/><path fill="#777e73" d="M43 31h64v15H43zM96 41h22v17H96zM110 55h14v25h-14z"/><path fill="#e2dfc8" d="M44 25h64v12H44zM102 35h14v14h-14z"/>'+end;
  if(entry.id==='bread')return start+'<path fill="#977344" d="M28 67h100v40H28zM38 51h80v16H38z"/><path fill="#ceaa67" d="M28 64h91v30H28zM39 49h68v18H39z"/><path fill="#f0d095" d="M49 53h9v19h-9zM77 53h9v19h-9z"/>'+end;
  return start+'<path fill="#7e6648" d="M32 40h96v77H32z"/><path fill="#b8965f" d="M32 40h80v63H32z"/><path fill="#d5b578" d="M32 30h80v15H32z"/><path fill="#65583e" d="M32 65h96v8H32zM43 43h7v57h-7zM94 43h7v57h-7z"/><path fill="#d9c997" d="M67 65h13v18H67z"/>'+end;
}
function scene(){return '<svg viewBox="0 0 620 300" preserveAspectRatio="xMidYMid slice" aria-hidden="true" shape-rendering="crispEdges"><path fill="#a9c5bd" d="M0 0h620v300H0z"/><path fill="#e5e7cf" d="M40 35h100v17H40zM57 23h57v12H57zM279 61h115v15H279z"/><path fill="#708873" d="M0 141h100v-24h110v41h130v-32h160v-25h120v199H0z"/><path fill="#75895a" d="M0 184h620v116H0z"/><path fill="#9b9a61" d="M250 180h65v25h-65zM200 205h115v25H200zM130 230h185v35H130zM75 265h240v35H75z"/><path fill="#655b3e" d="M107 105h21v111h-21zM30 119h17v77H30z"/><path fill="#426747" d="M58 62h130v40H58zM44 96h153v44H44zM0 100h83v48H0z"/><path fill="#5b7a4b" d="M73 55h100v27H73zM57 83h94v32H57z"/><path fill="#a7b778" d="M15 231h38v8H15zM330 193h36v8h-36zM354 248h25v8h-25z"/>'+(state.photo==='player'?'<path fill="#504732" d="M196 196h13v41h-13zM215 196h13v41h-13z"/><path fill="#698f8d" d="M188 160h47v39h-47z"/><path fill="#bdac88" d="M196 136h31v26h-31z"/><path fill="#4b493c" d="M196 130h31v10h-31z"/>':'')+'</svg>';}
function entries(){return state.book==='catalog'?M.filterCatalog(catalog,state):M.filterPoems(M.poems,state);}
function current(){return entries().find(e=>e.id===state.selected);}
function canSelectPoems(){return state.book==='poems'&&state.poemSection==='all';}
function renderList(){
  const items=entries();if(!items.some(e=>e.id===state.selected))state.selected=items[0]?.id??null;
  selectedPoems.retain(canSelectPoems()?items:[]);
  $('result-count').textContent=state.book==='catalog'?`${items.length} 項目 / 抜粋`:`${items.length} 句`;
  $('entry-list').innerHTML=items.length?items.map(e=>{
    const button=`<button class="entry" data-entry="${esc(e.id)}" aria-pressed="${e.id===state.selected}">${state.book==='catalog'?`<span class="mini">${icon(e)}</span><span><strong>${esc(e.raw.label)}</strong><p>${esc(M.summary(e))}</p></span>`:`<span class="poem-list-content"><span class="poem-list-body"><span class="poem-list-thumbnail" data-poem-thumbnail="${esc(e.id)}" aria-hidden="true"></span><strong class="poem-lines">${e.lines.map(line=>`<span>${esc(line)}</span>`).join('')}</strong></span><span class="poem-list-meta">${poemTags(e,true)}<time datetime="${esc(e.date)}">${esc(e.date.replaceAll('-','.'))}</time></span></span>`}</button>`;
    return !canSelectPoems()?button:`<div class="poem-index-row"><input type="checkbox" data-poem-select="${esc(e.id)}" aria-label="${esc(e.lines.join(' / '))} を削除対象に選択" ${selectedPoems.has(e.id)?'checked':''}>${button}</div>`;
  }).join(''):'<p class="empty">見つかりませんでした。<br>条件を変えて探してみてね。</p>';
  renderDetail();
}
function folio(){return `<div class="page-footer"><span>${state.book==='catalog'?'あんちょこ':'句集'} / 試作版</span></div>`;}
function renderDetail(){
  if(state.book==='catalog'&&state.catalogPane==='readings'&&window.DogidoReadings){window.DogidoReadings.renderPage();return;}
  const entry=current();
  if(!entry){$('detail').innerHTML='<div class="empty">表示する項目がありません。<br>「条件をクリア」で一覧に戻れます。</div>';}
  else if(state.book==='catalog')renderCatalog(entry);
  else renderPoem(entry);
  document.dispatchEvent(new Event('dogido-detail-rendered'));
}
function renderCatalog(e){
  const raw=e.raw,p=e.category==='mob'?raw.poetic:null,lead=p?.role||'';
  if(state.source==='poetic')state.source='base';
  let facts=p?[]:e.category==='biome'?[['分類',raw.group_label],['気温係数',raw.temperature],['降水量係数',raw.downfall]]:e.category==='structure'?[['分類',raw.group_label],['次元',raw.group_dimension],['バイオーム',raw.biomes?.join(' / ')]]:[['分類',M.categories[e.category]],['読み',raw.reading||'未収録']];
  facts=facts.filter(([key])=>key!=='分類'&&(key!=='読み'||raw.reading));
  let panel='';
  if(state.source==='base'){
    panel=M.catalogBasicInfo(e).map(section=>`<section class="catalog-note-section"><h3>${esc(section.label)}</h3>${section.paragraphs.map(text=>`<p class="catalog-note">${esc(text)}</p>`).join('')}</section>`).join('');
    if(p)panel+=`<section class="catalog-note-section poetic-keys"><h3>ポエティックキー</h3>${Object.entries({見た目:p.visual_tags,音:p.sound_tags,動き:p.motion_tags,場面:p.scene_tags,反応:p.reaction_tags,おかしみ:p.comic_tags}).map(([k,v])=>`<p>${k}：${(v||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join('')||'未収録'}</p>`).join('')}</section>`;
    if(!panel)panel='<p>この項目の説明はまだありません。</p>';
  }
  if(state.source==='notes')panel=renderCatalogNotes(e);
  const related=M.poems.filter(x=>x.catalog===e.id);
  if(state.source==='related')panel=related.length?'<p class="demo-label">導線の確認用：以下は仮の関連づけです。</p>'+related.map(x=>`<p><button class="link-button" data-related-poem="${x.id}">${esc(x.lines.join(' '))} ↗</button></p>`).join(''):'<p>この試作には関連する仮の句がありません。</p>';
  $('detail').innerHTML=`<div class="entry-heading"><div><h2 class="catalog-name"><span>${esc(raw.label)}</span><span class="name-slash"> / </span><span lang="en">${esc(M.catalogEnglishName(e.id))}</span></h2>${lead?`<p class="lead">${esc(lead)}</p>`:''}${raw.reading?`<p>${esc(raw.reading)}</p>`:''}</div></div><div class="illustration">${icon(e)}<span class="specimen-mark">${M.categories[e.category]}の頁</span><span class="art-caption">仮の挿絵</span></div>${facts.length?`<dl class="facts">${facts.map(([k,v])=>`<div><dt>${k}</dt><dd>${esc(v??'未収録')}</dd></div>`).join('')}</dl>`:''}<nav class="source-tabs" aria-label="情報の種類">${[['base','基本情報'],['notes','訂正・メモ'],['related','関連する句']].map(([key,label])=>`<button data-source="${key}" aria-pressed="${state.source===key}">${label}</button>`).join('')}</nav><div class="source-panel">${panel}</div>${folio()}`;
}
function renderPoem(e){
  const favorite=state.favorites.has(e.id),bookmarkLabel=favorite?'しおりを外す':'しおりを挟む';
  $('detail').innerHTML=`<div class="folio-head poem-page-meta">${poemTags(e)}<time datetime="${esc(e.date)}">${esc(e.date.replaceAll('-','.'))}</time><button class="favorite bookmark-button" id="favorite" aria-label="${bookmarkLabel}" title="${bookmarkLabel}" aria-pressed="${favorite}"><svg viewBox="0 0 28 36" width="28" height="36" aria-hidden="true" focusable="false"><path d="M5 2h18v31l-9-6-9 6Z"/></svg></button></div><div class="entry-heading"><p class="poem-preface">${esc(e.preface||'')}</p></div><div class="poem-overview"><div class="poem-scene"><span class="art-caption">まだ写真がありません</span></div><div class="poem-pair">${[["最初の句",e.originalLines||[]],[e.revised?"編集後の句":"現在の句（修正なし）",e.lines]].map(([label,lines])=>`<section class="verse-card"><h3>${label}</h3><div class="verse-lines">${lines.length?lines.map(line=>`<span>${esc(line)}</span>`).join(''):'<p>記録なし</p>'}</div></section>`).join('')}</div></div>${window.DogidoPoemRecord.render(e)}<h3 class="section-title"><label for="player-note">プレイヤーメモ</label></h3><textarea id="player-note" class="player-note" rows="5" placeholder="思ったことや、自分の句など、自由にどうぞ。">${esc(playerNotes.get(e.id))}</textarea>`;
}
function renderNavigation(){
  const isCatalog=state.book==='catalog';document.body.classList.toggle('poems',!isCatalog);
  document.querySelectorAll('[data-book]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.book===state.book)));
  $('book-title').innerHTML=isCatalog?'あんちょこ':'句集';$('volume').textContent=isCatalog?'世界を知る':'冒険を綴る';document.querySelector('.seal').textContent=isCatalog?'あんちょこ':'句集';
  $('categories').innerHTML=(isCatalog?Object.entries(M.categories):Object.entries(poemSections)).map(([key,label])=>{
    const open=state.listOpen&&key===(isCatalog?state.category:state.poemSection);
    return `<button ${isCatalog?'data-category':'data-poem-group'}="${key}" aria-pressed="${open}" aria-expanded="${open}" aria-controls="library-index">${key==='all'&&isCatalog?'すべての項目':label}<span aria-hidden="true">${open?'−':'＋'}</span></button>`;
  }).join('');
  indexPanel.id='library-index';indexPanel.hidden=!state.listOpen;
  document.querySelector('.desk').classList.toggle('list-open',state.listOpen);
  const selectedCategory=document.querySelector(isCatalog?`[data-category="${state.category}"]`:`[data-poem-group="${state.poemSection}"]`);
  selectedCategory.after(indexPanel);
  $('filters').hidden=isCatalog;$('index-title').textContent=isCatalog?(state.category==='all'?'すべての項目':M.categories[state.category]):poemSections[state.poemSection];$('index-kicker').textContent=isCatalog?'目 録':'句 の 一 覧';$('index-foot').textContent=isCatalog?'本体と共通のカタログ':'本体に保存された句';
  $('search').placeholder=isCatalog?'名前・読み・説明で探す':'句・場所・ことばで探す';$('search').setAttribute('aria-label',isCatalog?'名前・読み・説明で検索':'句・場所・ことばで検索');$('search').value=state.query;
  for(const key of ['biome','period','timeOfDay'])$(key).value=state[key];
}
function showCover(){
  document.body.classList.add('library-cover');
  document.body.classList.remove('library-photos');
  document.body.classList.remove('library-journeys');
  $('journey-library').hidden=true;
  document.body.classList.remove('book-intro');
  $('book-cover').hidden=true;
  $('photo-library').hidden=true;
  document.querySelectorAll('[data-book]').forEach(b=>b.setAttribute('aria-pressed','false'));
  history.replaceState(null,'','#cover');
}
function showBookCover(book){
  if(book==='journeys'){switchBook(book);return;}
  const covers={
    poems:{title:'句集',art:'poems-cover-pixel-v1.png',alt:'夕焼けの桜と、一匹のハチのドット絵'},
    catalog:{title:'あんちょこ',art:'catalog-cover-pixel-v1.png',alt:'草原のクリーパーと、黄色いタンポポのドット絵'},
    photos:{title:'フォトブック',art:'photos-cover-pixel-v1.png',alt:'海中のピンクのウーパールーパーと、気泡のドット絵'}
  };
  const cover=covers[book];if(!cover)return;
  switchBook(book);
  document.body.classList.add('book-intro');
  document.body.dataset.coverBook=book;
  $('book-cover').hidden=false;
  $('book-cover-title').textContent=cover.title;
  $('book-cover-art').src=`assets/${cover.art}`;
  $('book-cover-art').alt=cover.alt;
  placeBookCover();
  document.querySelectorAll('[data-book]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.book===book)));
  history.replaceState(null,'',`#${book}-cover`);
}
// 表紙でも既存の左メニューを使う。画像の要素だけを右側へ移動する。
function placeBookCover(){
  if(!document.body.classList.contains('book-intro'))return;
  const target=document.querySelector(document.body.dataset.coverBook==='photos'?'.album-preview':'.pages');
  target?.append($('book-cover'));
}
function closeBookCover(){
  if(!document.body.classList.contains('book-intro'))return;
  document.body.classList.remove('book-intro');$('book-cover').hidden=true;
  history.replaceState(null,'',`#${document.body.dataset.coverBook}`);
}
function switchBook(book,selected){
  document.body.classList.remove('library-cover');
  document.body.classList.remove('book-intro');
  $('book-cover').hidden=true;
  document.body.classList.toggle('library-photos',book==='photos');
  document.body.classList.toggle('library-journeys',book==='journeys');
  $('journey-library').hidden=book!=='journeys';
  $('photo-library').hidden=book!=='photos';
  if(book==='journeys'){
    history.replaceState(null,'','#journeys');
    document.querySelectorAll('[data-book]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.book==='journeys')));
    document.dispatchEvent(new Event('dogido-journeys-opened'));return;
  }
  if(book==='photos'){
    history.replaceState(null,'','#photos');
    document.querySelectorAll('[data-book]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.book==='photos')));
    document.dispatchEvent(new Event('dogido-album-opened'));return;
  }
  state.book=book;state.catalogPane='entry';state.listOpen=false;state.query='';state.category='all';state.biome='all';state.timeOfDay='all';state.period='all';state.poemSection='all';state.source='base';state.selected=selected||(book==='catalog'?defaultCatalogEntry:M.poems[0]?.id??null);history.replaceState(null,'',book==='poems'?'#poems':'#catalog');renderNavigation();renderList();
}
function clear(){state.query='';state.category='all';state.biome='all';state.timeOfDay='all';state.period='all';renderNavigation();renderList();}
document.addEventListener('click',event=>{
  const b=event.target.closest('button');if(!b)return;
  if(b.dataset.deleteKey!==undefined&&state.book==='catalog'){
    const i=Number(b.dataset.deleteKey);catalogNotes.remove(catalogNoteId(current()),i);renderDetail();
    const remaining=catalogNotes.get(catalogNoteId(current())).keys.length;
    (document.querySelector(`[data-delete-key="${Math.min(i,remaining-1)}"]`)||$('catalog-key-new')).focus({preventScroll:true});return;
  }
  if(b.dataset.book){showBookCover(b.dataset.book);return;}
  if(b.dataset.category||b.dataset.poemGroup||b.dataset.entry)closeBookCover();
  if(b.dataset.category){state.catalogPane='entry';state.listOpen=!(state.listOpen&&state.category===b.dataset.category);state.category=b.dataset.category;state.selected=null;renderNavigation();renderList();return;}
  if(b.dataset.poemGroup){state.listOpen=!(state.listOpen&&state.poemSection===b.dataset.poemGroup);state.poemSection=b.dataset.poemGroup;clear();return;}
  if(b.dataset.entry){state.catalogPane='entry';state.selected=b.dataset.entry;state.source='base';renderList();document.querySelector(`[data-entry="${state.selected}"]`)?.focus({preventScroll:true});return;}
  if(b.dataset.source){state.source=b.dataset.source;renderDetail();document.querySelector(`[data-source="${state.source}"]`)?.focus({preventScroll:true});return;}
  if(b.dataset.relatedPoem){switchBook('poems',b.dataset.relatedPoem);return;}
  if(b.dataset.relatedCatalog){switchBook('catalog',b.dataset.relatedCatalog);return;}
  if(b.dataset.photo){state.photo=b.dataset.photo;renderDetail();document.querySelector(`[data-photo="${state.photo}"]`).focus({preventScroll:true});return;}
  if(b.id==='favorite'){const id=state.selected;state.favorites.has(id)?state.favorites.delete(id):state.favorites.add(id);renderNavigation();renderList();$('favorite')?.focus({preventScroll:true});return;}
  if(b.id==='clear'){clear();$('search').focus();return;}
});
$('search').addEventListener('input',()=>{state.query=$('search').value;renderList();});
document.addEventListener('input',event=>{
  if(event.target.id==='catalog-key-new'&&state.book==='catalog')catalogNotes.setDraft(catalogNoteId(current()),event.target.value);
  if(event.target.id==='player-note'&&state.book==='poems')playerNotes.set(state.selected,event.target.value);
  if(state.book==='catalog'&&event.target.dataset.editKey!==undefined){
    const e=current(),key=catalogNoteId(e);
    catalogNotes.edit(key,Number(event.target.dataset.editKey),event.target.value);
    $('catalog-storage-status').textContent=catalogStorageMessage(e);
  }
});
document.addEventListener('submit',event=>{
  if(event.target.id!=='catalog-key-form')return;
  event.preventDefault();const input=$('catalog-key-new');
  if(!catalogNotes.add(catalogNoteId(current()),input.value)){$('catalog-key-feedback').textContent='ことばのメモを入力してください。';input.focus();return;}
  renderDetail();
  ($('catalog-key-new').disabled?document.querySelector('[data-reading-add] input'):$('catalog-key-new'))?.focus({preventScroll:true});
});
for(const key of ['biome','period','timeOfDay'])$(key).addEventListener('change',()=>{state[key]=$(key).value;renderList();});
function openLibraryRoute(){
  if(['#catalog-cover','#poems-cover','#photos-cover'].includes(location.hash))showBookCover(location.hash.slice(1,-6));
  else if(['#catalog','#poems','#photos','#journeys'].includes(location.hash))switchBook(location.hash.slice(1));
  else showCover();
}
window.addEventListener('hashchange',openLibraryRoute);
// Keep the underlying book ready for the photo/live adapters, but hidden on first launch.
renderNavigation();renderList();openLibraryRoute();
