/* Local visual prototype only: no microphone, server, adoption, or persistence. */
(() => {
  const get = id => document.getElementById(id);
  const names = {1: '上', 2: '中', 3: '下'};
  const targets = [...document.querySelectorAll('[data-edit-line]')];
  let editing = false;
  let selected = null;
  let audio = null;

  async function pip() {
    if (!get('edit-sound').checked) return;
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) throw new Error('Audio unavailable');
      audio ??= new Audio();
      if (audio.state === 'suspended') await audio.resume();
      if (audio.state !== 'running' || !get('edit-sound').checked) return;
      const oscillator = audio.createOscillator();
      const envelope = audio.createGain();
      const now = audio.currentTime;
      oscillator.type = 'sine';
      oscillator.frequency.setValueAtTime(1100, now);
      envelope.gain.setValueAtTime(0, now);
      envelope.gain.linearRampToValueAtTime(0.045, now + 0.004);
      envelope.gain.exponentialRampToValueAtTime(0.001, now + 0.055);
      oscillator.connect(envelope);
      envelope.connect(audio.destination);
      oscillator.start(now);
      oscillator.stop(now + 0.06);
      oscillator.onended = () => { oscillator.disconnect(); envelope.disconnect(); };
    } catch {
      get('edit-feedback').textContent = '操作音は利用できません。表示だけで試せます。';
    }
  }

  function renderSelection() {
    get('edit-targets').hidden = !editing;
    get('selection-caption').hidden = !editing;
    get('selection-caption').textContent = selected ? `${names[selected]}の句 選択中` : '直す場所を選ぶ';
    for (const button of targets) {
      const chosen = editing && Number(button.dataset.editLine) === selected;
      button.setAttribute('aria-pressed', String(chosen));
      const verse = get(`verse-${button.dataset.editLine}`);
      verse.classList.toggle('edit-selected', chosen);
      if (chosen) verse.setAttribute('aria-label', `${names[selected]}の句、選択中：${verse.textContent}`);
      else verse.removeAttribute('aria-label');
    }
    get('edit-form').hidden = !editing || selected === null;
    get('edit-replacement').disabled = !editing || selected === null;
    get('edit-apply').disabled = !editing || selected === null;
    get('edit-intent').textContent = editing ? '選択表示を閉じる' : '句を直す';
    get('edit-intent').setAttribute('aria-pressed', String(editing));
  }

  function closeEditor() {
    editing = false;
    selected = null;
    get('edit-feedback').textContent = '';
    renderSelection();
  }

  get('edit-intent').addEventListener('click', () => {
    if (editing) { closeEditor(); return; }
    // Selection cues appear immediately even if the scroll was still fading in.
    get('scroll').getAnimations().forEach(animation => animation.cancel());
    editing = true;
    selected = null;
    get('edit-feedback').textContent = '上・中・下から直す場所を選んでください。';
    renderSelection();
    void pip();
  });

  for (const button of targets) button.addEventListener('click', () => {
    if (!editing) return;
    const next = Number(button.dataset.editLine);
    if (selected === next) return;
    selected = next;
    get('edit-replacement').value = get(`line-${selected}`).value;
    get('replacement-label').textContent = `${names[selected]}の句の置き換え（仮）`;
    get('edit-feedback').textContent = `${names[selected]}の句を選択しました。`;
    renderSelection();
    void pip();
  });

  get('edit-form').addEventListener('submit', event => {
    event.preventDefault();
    if (!editing || selected === null) return;
    const replacement = get('edit-replacement').value.trim();
    if (!replacement || [...replacement].length > 7) {
      get('edit-feedback').textContent = '表示試験では1〜7文字を入力してください。';
      return;
    }
    const input = get(`line-${selected}`);
    input.value = replacement;
    input.dispatchEvent(new Event('input', {bubbles: true}));
    renderSelection();
    get('edit-feedback').textContent = `${names[selected]}の句の表示だけを更新しました。未保存です。`;
    void pip();
  });

  document.addEventListener('preview-mode-change', event => {
    if (event.detail !== 'workshop') closeEditor();
  });
  for (const line of [1, 2, 3]) get(`line-${line}`).addEventListener('input', () => {
    if (editing && selected === line) {
      get('edit-replacement').value = get(`line-${line}`).value;
      renderSelection();
    }
  });
  renderSelection();
})();
