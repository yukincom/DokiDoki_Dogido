/* 保存記録の表示だけ。操作ログや未発話文から会話を再構成しない。 */
(function(root){
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const texts=atoms=>(Array.isArray(atoms)?atoms:[]).map(atom=>typeof atom==='string'?atom:atom?.text).filter(text=>typeof text==='string'&&text.trim());
  function time(value){const date=new Date(value);return Number.isNaN(date.valueOf())?'時刻未記録':new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',hour:'2-digit',minute:'2-digit'}).format(date);}
  function captureModelName(artifact){
    // APIの別名ではなく、採取時に照合したモデル資産から表示名を取る。
    const path=[artifact?.identity?.model?.path,artifact?.model?.assets?.resolved_model_path,artifact?.model?.path].find(value=>typeof value==='string'&&value.trim());
    if(!path)return 'モデル未記録';
    const parts=path.replaceAll('\\','/').split('/').filter(Boolean);
    const snapshot=parts.lastIndexOf('snapshots'),repository=parts[snapshot-1];
    if(snapshot>0&&repository?.startsWith('models--'))return repository.slice(8).replaceAll('--','/');
    return parts.at(-1)?.replace(/\.gguf$/i,'')||'モデル未記録';
  }
  function internalCapture(e){
    const capture=e.internalCapture,requests=Array.isArray(capture?.requests)?capture.requests:[];
    if(!requests.length)return '<p class="record-missing">内部表現の記録はありません。この句には採取記録が保存されていません。</p>';
    const stages={haiku_irony:'見どころ',haiku_scene:'情景',haiku_draft:'句の生成',haiku_line_regeneration:'行の再生成'};
    const states={complete:'採取完了',partial:'一部採取',failed:'採取失敗',cancelled:'採取取消',missing:'採取ファイルなし',invalid:'検証不成立',request_failed:'生成要求失敗',requested:'結果未記録'};
    return `<p class="record-prose">発句中に生成した元のトークンに対応する、各層の数値記録です。整形・読みの補完後の句と一対一には対応しません。</p><ol class="capture-records">${requests.map(r=>{
      const a=r.artifact,shape=a?.shape,numeric=a?.hidden_verified===true&&['complete','partial'].includes(r.status)&&Array.isArray(shape)&&shape.length===3&&shape.every(n=>Number.isSafeInteger(n)&&n>0);
      const index=Number.isSafeInteger(r.request_index)&&r.request_index>0?r.request_index:null;
      const base=e.id&&index?`/api/poems/${encodeURIComponent(e.id)}/internal-capture/${index}`:null;
      const downloadable=a&&['complete','partial','failed','cancelled'].includes(r.status)&&base;
      const bytes=Number(a?.hidden_bytes),size=Number.isFinite(bytes)&&bytes>0?`${(bytes/1048576).toFixed(1)} MiB`:'';
      const state=states[r.status]||'状態不明';
      return `<li><h4>${esc(stages[r.kind]||r.kind)} <span>要求 ${esc(r.request_index)} · ${state}</span></h4><p>${esc(captureModelName(a))}${a?.backend?` ／ ${esc(a.backend)}`:''}</p>${numeric?`<p>${shape[0]} トークン × ${shape[1]} 層 × ${shape[2]} 次元${size?` · ${size}`:''}</p>`:'<p class="record-missing">検証済みの数値データはありません。</p>'}${downloadable?`<p class="capture-downloads"><a href="${esc(base)}/manifest" download>採取記録を保存</a>${numeric?`<a href="${esc(base)}/hidden" download>数値データを保存</a>`:''}</p>`:''}<details class="capture-identifiers"><summary>照合情報</summary><dl><dt>応答ID</dt><dd>${esc(r.response_id||'未記録')}</dd><dt>要求ID</dt><dd>${esc(r.request_id||'未記録')}</dd>${a?.manifest_sha256?`<dt>採取記録のSHA-256</dt><dd>${esc(a.manifest_sha256)}</dd>`:''}${numeric?`<dt>数値データのSHA-256</dt><dd>${esc(a.hidden_sha256)}</dd><dt>採取位置</dt><dd>${esc(a.boundary)}</dd>`:''}${r.error||a?.error?`<dt>採取時の記録</dt><dd>${esc(r.error||a.error)}</dd>`:''}</dl></details></li>`;
    }).join('')}</ol>`;
  }
  function render(e){
    const m=e.materials||{},dialogue=Array.isArray(e.dialogue)?e.dialogue:[];
    const sources=(Array.isArray(m.line_sources)?m.line_sources:[]).map(row=>({line:row.text,sources:texts(row.sources)}));
    const candidates=texts(m.source_atoms);
    return `<section class="poem-record" aria-label="句の制作記録"><details open><summary>対話の記録 <span>${dialogue.length}往復</span></summary>${dialogue.length?`<ol class="workshop-dialogue">${dialogue.map(turn=>`<li><time datetime="${esc(turn.time)}" title="${esc(turn.time)}">${time(turn.time)}</time><p><b>プレイヤー</b><span>${esc(turn.player)}</span></p><p><b>ドギド</b><span>${esc(turn.dogido)}</span></p></li>`).join('')}</ol>`:'<p class="record-missing">保存された対話はありません。</p>'}</details><details><summary>発句の材料</summary>${sources.length?`<h4>行ごとの出典</h4><ul class="line-sources">${sources.map(row=>`<li><strong>${esc(row.line)}</strong><p>${row.sources.map(esc).join(' ／ ')||'出典の本文は未記録'}</p></li>`).join('')}</ul>`:''}${candidates.length?`<h4>発句時の候補材料</h4><ul>${candidates.map(text=>`<li>${esc(text)}</li>`).join('')}</ul>`:''}${m.interpretation?`<h4>発句時の解釈</h4><p class="record-prose">${esc(m.interpretation)}</p>`:''}${!sources.length&&!candidates.length&&!m.interpretation?'<p class="record-missing">材料の記録はありません。</p>':''}</details><details class="poem-internal-representations"><summary>内部表現</summary>${internalCapture(e)}</details></section>`;
  }
  const api={render,time};if(typeof module!=='undefined'&&module.exports)module.exports=api;else root.DogidoPoemRecord=api;
})(typeof window==='undefined'?globalThis:window);
