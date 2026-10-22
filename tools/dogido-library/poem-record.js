/* 保存記録の表示だけ。操作ログや未発話文から会話を再構成しない。 */
(function(root){
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const texts=atoms=>(Array.isArray(atoms)?atoms:[]).map(atom=>typeof atom==='string'?atom:atom?.text).filter(text=>typeof text==='string'&&text.trim());
  function time(value){const date=new Date(value);return Number.isNaN(date.valueOf())?'時刻未記録':new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',hour:'2-digit',minute:'2-digit'}).format(date);}
  function render(e){
    const m=e.materials||{},dialogue=Array.isArray(e.dialogue)?e.dialogue:[];
    const sources=(Array.isArray(m.line_sources)?m.line_sources:[]).map(row=>({line:row.text,sources:texts(row.sources)}));
    const candidates=texts(m.source_atoms);
    return `<section class="poem-record" aria-label="句の制作記録"><details open><summary>対話の記録 <span>${dialogue.length}往復</span></summary>${dialogue.length?`<ol class="workshop-dialogue">${dialogue.map(turn=>`<li><time datetime="${esc(turn.time)}" title="${esc(turn.time)}">${time(turn.time)}</time><p><b>プレイヤー</b><span>${esc(turn.player)}</span></p><p><b>ドギド</b><span>${esc(turn.dogido)}</span></p></li>`).join('')}</ol>`:'<p class="record-missing">保存された対話はありません。</p>'}</details><details><summary>発句の材料</summary>${sources.length?`<h4>行ごとの出典</h4><ul class="line-sources">${sources.map(row=>`<li><strong>${esc(row.line)}</strong><p>${row.sources.map(esc).join(' ／ ')||'出典の本文は未記録'}</p></li>`).join('')}</ul>`:''}${candidates.length?`<h4>発句時の候補材料</h4><ul>${candidates.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`:''}${m.interpretation?`<h4>発句時の解釈</h4><p class="record-prose">${esc(m.interpretation)}</p>`:''}${!sources.length&&!candidates.length&&!m.interpretation?'<p class="record-missing">材料の記録はありません。</p>':''}</details></section>`;
  }
  const api={render,time};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DogidoPoemRecord=api;
})(typeof window==='undefined'?globalThis:window);
