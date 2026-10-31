// パワーのある実行環境のため、繊細なコントロールは不要。会話のテンポを止める処理を入れるな。
// A presentation option on the existing /dogido page. It never changes records.
(() => {
  if (new URLSearchParams(window.location.search).get('view') !== 'utterances') return;
  if (document.documentElement.classList.contains('utterances-only')) return;
  document.documentElement.classList.add('utterances-only');
  const style = document.createElement('style');
  style.textContent = `
    .utterances-only .runtime-info,
    .utterances-only .diagnostic-panel,
    .utterances-only #reference-panel,
    .utterances-only .page > p[role="status"] { display: none; }
    .utterances-only .layout { display: block; }
  `;
  document.head.appendChild(style);
  const footnote = document.querySelector('.footnote');
  if (footnote) footnote.textContent = '発言はこのサーバーの起動中だけ保持され、再起動すると消去されます。';
})();
