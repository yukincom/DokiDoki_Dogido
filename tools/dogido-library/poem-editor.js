/* 本棚の本人編集。入力欄は保存で作り直さず、標準のIME・コピー・undoを維持する。 */
(function(root){
  'use strict';
  const head=poem=>poem.revised?poem.current.id:null;
  function createDraft(poem){
    let base=[...poem.lines],revision=head(poem),pending=null,value=base.join('\n');
    return {
      get value(){return value;},set value(text){value=text;},
      hasChanges:()=>Boolean(pending)||value!==base.join('\n'),
      prepare(makeId){
        // 結果不明の再送では、保存中に追加入力されても同じ要求を先に照合する。
        if(pending)return pending;
        if(value===base.join('\n'))return null;
        const lines=value.split('\n');
        if(lines.length!==3||lines.some(line=>!line.trim()))throw Error('句は改行で三行に分けて入力してください。');
        pending={id:poem.id,requestId:makeId(),expectedRevisionId:revision,expectedLines:[...base],lines};
        return pending;
      },
      accept(saved){base=[...saved.lines];revision=head(saved);pending=null;},
      rejected(){pending=null;}
    };
  }
  if(typeof module!=='undefined'&&module.exports){module.exports={createDraft};return;}
  const boot=root.DOGIDO_BOOTSTRAP;if(!boot)return;
  const drafts=new Map();
  const changed=()=>document.dispatchEvent(new Event('dogido-poems-changed'));
  function getDraft(poem){
    let entry=drafts.get(poem.id);
    if(!entry){
      const model=createDraft(poem),input=document.createElement('textarea');
      input.className='poem-edit-input';input.wrap='off';input.spellcheck=false;
      input.setAttribute('aria-label','現在の句を編集。改行で三行に分けて入力');
      input.setAttribute('autocapitalize','off');input.value=model.value;
      entry={model,input,open:false,version:head(poem)};
      input.addEventListener('input',()=>{model.value=input.value;entry.refresh?.();changed();});
      drafts.set(poem.id,entry);
    }else if((!entry.open||entry.input.getClientRects().length===0)&&!entry.model.hasChanges()&&entry.version!==head(poem)){
      // 閉じた／別ページにある未変更の入力だけを最新の版へ揃える。
      // 表示中の入力と未保存の下書きは、カーソル・undo・本人の本文を保つ。
      drafts.delete(poem.id);return getDraft(poem);
    }
    return entry;
  }
  function decorate(){
    if(state.book!=='poems')return;
    const poem=current(),card=document.querySelector('.poem-pair>.verse-card:last-child');
    if(!poem||!card||card.querySelector('.poem-edit-button'))return;
    const draft=getDraft(poem),button=document.createElement('button'),help=document.createElement('p');
    card.classList.add('poem-current-card');
    button.type='button';button.className='poem-edit-button';button.disabled=boot.status.active||poem.readOnly===true;
    button.innerHTML='<svg viewBox="0 0 32 32" width="30" height="30" aria-hidden="true" focusable="false"><path d="m7 21 4 4L4 28Zm2-2L22 6l5 5-13 13Zm15-15 2-2a2 2 0 0 1 3 0l1 1a2 2 0 0 1 0 3l-2 2Z"/></svg>';
    help.className='poem-edit-help';help.textContent='改行で三行 · ⌘S 保存';
    const update=()=>{
      const label=draft.open?'句の編集を閉じる（未保存の入力は保持）':'現在の句を編集';
      button.setAttribute('aria-label',label);button.title=label;button.setAttribute('aria-pressed',String(draft.open));
      card.classList.toggle('is-editing',draft.open);draft.input.hidden=!draft.open;help.hidden=!draft.open;
      card.querySelector('h3').textContent=draft.model.hasChanges()?'現在の句（未保存）':poem.revised?'編集後の句':'現在の句（修正なし）';
      if(!draft.open)card.querySelector('.verse-lines').innerHTML=draft.model.value.split('\n').map(line=>`<span>${esc(line)}</span>`).join('');
    };
    draft.refresh=update;
    button.addEventListener('click',()=>{draft.open=!draft.open;update();if(draft.open)draft.input.focus({preventScroll:true});});
    card.append(draft.input,help,button);update();
  }
  function updateSaved(poem){
    const entry=M.poems.find(p=>p.id===poem.id);if(entry)Object.assign(entry,poem,{biome:M.biomes[poem.biomeId]});
    // 保存のためにdetail全体を再描画しない。各入力欄とネイティブundo履歴を残す。
    document.querySelectorAll('[data-entry]').forEach(button=>{
      if(button.dataset.entry===poem.id){
        const lines=button.querySelector('.poem-lines');if(lines)lines.innerHTML=poem.lines.map(line=>`<span>${esc(line)}</span>`).join('');
      }
    });
    if(state.book==='poems'&&state.selected===poem.id){
      const card=document.querySelector('.poem-current-card');
      if(card)drafts.get(poem.id)?.refresh?.();
    }
  }
  async function save(){
    for(const [id,draft] of drafts){
      if(!draft.model.hasChanges())continue;
      const sent=draft.model.prepare(()=>crypto.randomUUID());
      const response=await fetch('/api/poems/current',{method:'PUT',headers:{'Content-Type':'application/json','X-Dogido-Library':boot.csrf},body:JSON.stringify(sent)});
      const data=await response.json();
      if(!response.ok){if(response.status>=400&&response.status<500)draft.model.rejected();throw Error(data.detail||'句の変更を保存できませんでした。入力は残っています。');}
      if(data.poem?.id!==id)throw Error('句の保存結果を確認できませんでした。入力は残っています。');
      draft.model.accept(data.poem);draft.version=head(data.poem);updateSaved(data.poem);
    }
    changed();
  }
  root.DogidoPoemEditor={hasChanges:()=>[...drafts.values()].some(d=>d.model.hasChanges()),isOpen:()=>[...drafts.values()].some(d=>d.open&&d.input.getClientRects().length>0),save};
  document.addEventListener('dogido-detail-rendered',decorate);decorate();
})(typeof window==='undefined'?globalThis:window);
