const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const M=require('./model.js'),record=require('./poem-record.js'),app=fs.readFileSync(__dirname+'/app.js','utf8');
test('rating has no model, UI, sort, styling or save contract',()=>{
 assert.equal(M.createRatings,undefined);
 for(const name of ['app.js','model.js','live-library.js','poem-metadata.css','readability.css'])assert.doesNotMatch(fs.readFileSync(__dirname+'/'+name,'utf8'),/rating|ratings|stars|星で評価/);
});
test('records escape stored text, preserve repetition, and never render operation events',()=>{
 const e={materials:{interpretation:'<解釈>',source_atoms:[{text:'<候補>'}],line_sources:[{text:'最初の行',sources:[{text:'<出典>'}]}]},dialogue:[{time:'2026-10-19T01:23:00Z',player:'<質問>',dogido:'<返答>'},{time:'2026-10-19T01:24:00Z',player:'<質問>',dogido:'<返答>'}],history:[{action:'SECRET_OPERATION'}]};
 const before=JSON.stringify(e),html=record.render(e);
 assert.match(html,/&lt;解釈&gt;/);assert.match(html,/&lt;候補&gt;/);assert.match(html,/&lt;出典&gt;/);assert.match(html,/10:23/);
 assert.equal((html.match(/&lt;質問&gt;/g)||[]).length,2);assert.doesNotMatch(html,/<質問>|SECRET_OPERATION/);assert.equal(JSON.stringify(e),before);
 const empty=record.render({});assert.match(empty,/対話.*ありません/);assert.match(empty,/材料の記録はありません/);assert.equal(record.time('invalid'),'時刻未記録');
});
test('original and current verses sit together next to the photo, before records and notes',()=>{
 const detail={innerHTML:''},e={id:'root:1',date:'2026-10-19',lines:['<修正>','二','三'],originalLines:['<最初>','元二','元三'],revised:true},scope={e,window:{DogidoPoemRecord:record},state:{favorites:new Set()},playerNotes:{get:()=>''},poemTags:()=>'',esc:value=>String(value??'').replaceAll('<','&lt;').replaceAll('>','&gt;'),$:()=>detail};
 vm.runInNewContext(app.slice(app.indexOf('function renderPoem('),app.indexOf('function renderNavigation('))+'renderPoem(e);',scope);
 const html=detail.innerHTML;assert.match(html,/最初の句/);assert.match(html,/編集後の句/);assert.match(html,/&lt;最初&gt;/);assert.match(html,/&lt;修正&gt;/);
 assert(html.indexOf('最初の句')<html.indexOf('編集後の句'));
 const layout=fs.readFileSync(__dirname+'/poem-record.css','utf8');
 assert.match(layout,/\.poem-pair>\.verse-card:first-child\{grid-column:2;grid-row:1\}/);
 assert.match(layout,/\.poem-pair>\.verse-card:last-child\{grid-column:1;grid-row:1\}/);
 assert(html.indexOf('poem-overview')<html.indexOf('poem-record'));assert(html.indexOf('poem-record')<html.indexOf('id="player-note"'));assert.equal((html.match(/datetime="2026-10-19"/g)||[]).length,1);
});
test('internal representations have a collapsed disclosure after materials and do not invent measurements',()=>{
 const html=record.render({materials:{interpretation:'これは発句の解釈'}});
 assert.match(html,/<details class="poem-internal-representations"><summary>内部表現<\/summary>/);
 assert(html.indexOf('発句の材料')<html.indexOf('<summary>内部表現'));
 assert.match(html,/内部表現の記録はありません/);assert.match(html,/この句には採取記録が保存されていません/);
 assert.equal((html.match(/これは発句の解釈/g)||[]).length,1);
});
test('list toolbar keeps clear above count beside icons and bookmark aligns right in the metadata header',()=>{
 const controls=fs.readFileSync(__dirname+'/bookshelf-controls.css','utf8'),metadata=fs.readFileSync(__dirname+'/poem-metadata.css','utf8');
 assert.match(controls,/\.poem-menu-actions>\.result-meta\{[^}]*flex-direction:column;[^}]*margin:0 0 0 auto/);
 assert.match(controls,/\.poem-menu-actions #clear\{order:0/);assert.match(controls,/\.poem-menu-actions #result-count\{order:1/);
 assert.match(metadata,/\.poem-page-meta \.bookmark-button\{margin-left:auto/);
 assert.doesNotMatch(fs.readFileSync(__dirname+'/live-library.js','utf8'),/星は並べ替え用です|ドギドの学習には使いません/);
 assert.match(fs.readFileSync(__dirname+'/readability.css','utf8'),/\.desk,\.desk\.list-open \{ grid-template-columns:340px minmax\(0,1fr\); \}/);
});
test('capture records show stages and partial dimensions with escaped links and no invented final-verse alignment',()=>{
 const e={id:'root:<poem>',internalCapture:{requests:[{request_index:2,request_id:'<job>',response_id:'<reply>',kind:'haiku_scene',status:'partial',response_model:'default_model',artifact:{model:{path:'/models/<model>.gguf'},backend:'llama.cpp',hidden_verified:true,shape:[32,30,2816],hidden_bytes:10813440,hidden_sha256:'abc',manifest_sha256:'def',boundary:'decoder_layer_output_pre_final_norm'}}]},internalCaptureStorage:{requests:[{request_index:2,response_id:'<reply>',state:'saved'}]}};
 const before=JSON.stringify(e),html=record.render(e);
 assert.match(html,/情景/);assert.match(html,/一部採取/);assert.match(html,/32 トークン × 30 層 × 2816 次元/);
 assert.doesNotMatch(html,/保全|default_model/);assert.match(html,/数値データを保存/);assert.match(html,/root%3A%3Cpoem%3E\/internal-capture\/2\/hidden/);
 assert.match(html,/一対一には対応しません/);assert.match(html,/&lt;model&gt;/);assert.doesNotMatch(html,/<model>|<reply>|<job>/);
 assert.match(html,/<summary>前 — 応答開始直前<\/summary>/);assert.match(html,/<summary>中 — 生成中<\/summary>/);
 assert.match(html,/生成末尾までの数値は記録されていません/);assert.equal((html.match(/class="capture-slice"/g)||[]).length,2);
 assert.doesNotMatch(html,/<details class="capture-slice"[^>]*\bopen\b/);
 assert.equal(JSON.stringify(e),before);
});
test('complete capture exposes its first, selectable middle and actual last position without rewriting output',()=>{
 const text='{"description":"<生の生成文>"}';
 const html=record.render({id:'p',internalCapture:{requests:[{request_index:1,kind:'haiku_irony',status:'complete',api_output:{text},artifact:{hidden_verified:true,shape:[300,30,2816]}}]}});
 assert.equal((html.match(/class="capture-slice"/g)||[]).length,3);
 assert.match(html,/max="300" value="1" readonly/);assert.match(html,/max="300" value="150"/);assert.match(html,/max="300" value="300" readonly/);
 assert.match(html,/&quot;description&quot;:&quot;&lt;生の生成文&gt;&quot;/);
 assert.doesNotMatch(html,/未採取|生成末尾までの数値は記録されていません/);
});
test('failed and missing captures never claim numeric data or display archive status',()=>{
 for(const status of ['failed','cancelled','missing','invalid','request_failed','requested']){
  const e={id:'root:p',internalCapture:{requests:[{request_index:1,kind:'haiku_draft',status,error:'<reason>',artifact:{hidden_verified:false}}]},internalCaptureStorage:{requests:[{request_index:1,state:'error'}]}};
  const html=record.render(e);assert.doesNotMatch(html,/数値データを保存|トークン ×|保全/);assert.match(html,/&lt;reason&gt;/);assert.match(html,/検証済みの数値データはありません/);
 }
});
test('capture models use recorded GGUF or MLX assets instead of API aliases',()=>{
 for(const [artifact,expected] of [
  [{identity:{model:{path:'/models/gemma-4-26B-A4B-it-qat-UD-Q4_K_XL.gguf'}},model:{name:'default_model',path:'/models/alias.gguf'}},'gemma-4-26B-A4B-it-qat-UD-Q4_K_XL'],
  [{model:{assets:{resolved_model_path:'/models/gemma-4-26B-A4B-it-4bit'}}},'gemma-4-26B-A4B-it-4bit'],
  [{model:{assets:{resolved_model_path:'/cache/models--mlx-community--gemma-4-26B-A4B-it-4bit/snapshots/abcdef'}}},'mlx-community/gemma-4-26B-A4B-it-4bit'],
  [{model:{name:'default_model'}},'モデル未記録']
 ]){
  const html=record.render({internalCapture:{requests:[{kind:'haiku_draft',request_index:1,status:'requested',requested_model:'default_model',response_model:'custom_api_alias',artifact}]}});
  assert(html.includes(expected));assert.doesNotMatch(html,/default_model|custom_api_alias|\/models\/|abcdef/);
 }
});

test('candidate controls keep raw numbers separately folded with no generated interpretation',()=>{
 const html=record.render({id:'p',internalCapture:{requests:[{request_index:1,kind:'haiku_irony',status:'complete',artifact:{hidden_verified:true,shape:[300,30,2816]}}]}});
 assert.equal((html.match(/class="j-lens"/g)||[]).length,3);
 assert.equal((html.match(/class="capture-raw-slice"/g)||[]).length,3);
 assert.match(html,/試験的な読出し/);assert.match(html,/上位16候補/);
 assert.match(html,/value="14" selected/);assert.match(html,/29">30 \/ 30（最終層）/);
 assert.doesNotMatch(html,/<details class="capture-raw-slice"[^>]* open/);
 assert.doesNotMatch(html,/<details[^>]*\bopen\b/);
 assert.equal((html.match(/class="j-lens-translate"/g)||[]).length,3);
 assert.match(html,/>文<\/button>/);
});
