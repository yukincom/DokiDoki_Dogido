/* 冒険のあらすじ・短い未要約対話と句へのリンクを表示する。記録メモは既存の本棚保存処理へ渡す。 */
(function(){
  'use strict';
  const boot=window.DOGIDO_BOOTSTRAP;if(!boot)return;
  const page=$('journey-library');
  let records=boot.journeys||[],selected=null,query='';
  const notes=new Map(records.map(r=>[r.id,boot.player.journeys?.[r.id]?.note||'']));
  const dateLabel=date=>/^\d{4}-\d{2}-\d{2}$/.test(date)?date.replace(/^(\d+)-(\d+)-(\d+)$/,'$1年$2月$3日'):date;
  function dateTimeLabel(record){
    const time=new Date(record.started_at);
    const clock=Number.isNaN(time.getTime())?'':time.toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit',hourCycle:'h23'});
    return `${dateLabel(record.date)}${clock?' '+clock:''}`;
  }
  function firstSentence(text){const end=text.indexOf('。');return end<0?text:text.slice(0,end+1);}
  function entries(){return records.filter(r=>[r.date,dateTimeLabel(r),r.player,...r.synopses.map(p=>p.text),...(r.raw_tail||[]).map(p=>p.text),notes.get(r.id)||''].join('\n').includes(query));}
  function markActive(){
    if(!document.body.classList.contains('library-journeys'))return;
    document.querySelectorAll('[data-book]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.book==='journeys')));
  }
  function detail(record){
    if(!record)return '<div class="journey-empty"><p>旅の記録はまだありません。</p><p>ドギドとの冒険を終えると、ここにあらすじが残ります。</p></div>';
    const paragraphs=record.synopses.map(p=>`<p>${esc(p.text)}</p>`).join('');
    const raw=(record.raw_tail||[]).map(row=>{
      const speaker=row.role==='user'?(record.player||'あなた'):row.role==='assistant'?'ドギド':'出来事';
      return `<p><strong>${esc(speaker)}</strong><span>${esc(row.text)}</span></p>`;
    }).join('');
    const dialogue=raw?`<section class="journey-dialogue" aria-label="未要約のやりとり"><h3 class="section-title">未要約のやりとり</h3>${raw}</section>`:'';
    const poems=record.poems.map(link=>{
      const poem=M.poems.find(p=>p.id===link.id);
      return `<li>${poem?`<button type="button" class="journey-poem-link" data-related-poem="${esc(link.id)}">${poem.lines.map(esc).join(' / ')}<span aria-hidden="true"> →</span></button>`:`<span>${esc(link.text)}</span><small>句集の保存先を確認できません</small>`}</li>`;
    }).join('');
    return `<header class="journey-heading"><h2><time datetime="${esc(record.started_at)}">${esc(dateTimeLabel(record))}</time></h2>${!record.ended_at?'<p class="journey-status">途中までの記録</p>':''}</header><section class="journey-synopsis" aria-label="冒険のあらすじ">${paragraphs||(!raw?'<p>この冒険のあらすじは残っていません。</p>':'')}</section>${dialogue}${record.unfinished?'<p class="journey-status">終了時にまとめられなかった対話は収録していません。</p>':''}<section class="journey-poems"><h3 class="section-title">発句</h3>${poems?`<ul>${poems}</ul>`:'<p>この冒険で届けた句はありません。</p>'}</section><section class="journey-notes"><h3 class="section-title"><label for="journey-note">記録メモ</label></h3><textarea id="journey-note" rows="7" maxlength="4000" data-journey-note="${esc(record.id)}">${esc(notes.get(record.id)||'')}</textarea></section>`;
  }
  function render(){
    const items=entries();if(!items.some(r=>r.id===selected))selected=items[0]?.id??null;
    // 検索欄は置換しない。日本語変換と標準のUndoを画面更新で切らない。
    if(!$('journey-search'))page.innerHTML=`<div class="journey-columns"><aside class="binding journey-index"><header><h1>旅の記録</h1></header><div class="journey-index-paper"><label class="journey-search-label" for="journey-search">記録を探す</label><input id="journey-search" type="search" placeholder="日付・本文・メモで探す"><p id="journey-count" class="journey-count" role="status"></p><nav id="journey-list" class="journey-list" aria-label="冒険の日付"></nav></div></aside><article id="journey-detail" class="journey-detail" aria-label="選択した冒険"></article></div>`;
    $('journey-search').value=query;
    $('journey-count').textContent=`${items.length} 件`;
    $('journey-list').innerHTML=items.map(r=>`<button type="button" data-journey-select="${esc(r.id)}" aria-pressed="${r.id===selected}"><time datetime="${esc(r.started_at)}">${esc(dateTimeLabel(r))}</time><span>${esc(firstSentence(r.synopses[0]?.text||r.raw_tail?.[0]?.text||r.poems[0]?.text||'冒険の記録'))}</span></button>`).join('');
    $('journey-detail').innerHTML=detail(items.find(r=>r.id===selected));
    markActive();
    document.dispatchEvent(new CustomEvent('dogido-journey-rendered'));
  }
  window.DogidoJourneys={
    changes(){return records.flatMap(r=>{const note=notes.get(r.id)||'';return note===(boot.player.journeys?.[r.id]?.note||'')?[]:[{section:'journeys',key:r.id,value:{note}}];});},
    refresh(next,saved){records=next;boot.journeys=next;notes.clear();for(const r of records)notes.set(r.id,saved[r.id]?.note||'');if(document.body.classList.contains('library-journeys'))render();},
    markActive,
  };
  document.addEventListener('dogido-journeys-opened',render);
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-journey-select]');if(!button)return;
    selected=button.dataset.journeySelect;render();
    page.querySelector(`[data-journey-select="${selected}"]`)?.focus({preventScroll:true});
  });
  document.addEventListener('input',event=>{
    if(event.target.id==='journey-search'){query=event.target.value;render();}
    if(event.target.dataset.journeyNote)notes.set(event.target.dataset.journeyNote,event.target.value);
  });
  if(document.body.classList.contains('library-journeys'))render();
})();
