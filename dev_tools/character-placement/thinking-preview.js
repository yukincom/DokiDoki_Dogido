// Ten authored PNGs, composited at exactly the same canvas origin.
// Body, tail and pupils swap independently; face/hands are never deformed.
(() => {
  const byId = id => document.getElementById(id);
  const ns = 'http://www.w3.org/2000/svg';
  const {WIDTH, HEIGHT, LAYER_NAMES: names, frameAt} = window.DogidoThinkingAnimation;
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${WIDTH} ${HEIGHT}`);
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', '川柳を考えるドギド。描き分けた体と尻尾、左右の目線が別々に動く');
  svg.id = 'thinking-pose';
  svg.setAttribute('hidden', '');
  const base = '../../adapter/minecraft-fabric/src/main/resources/assets/dogido/textures/gui/thinking/';
  const layers = Object.fromEntries(names.map(name => {
    const layer = document.createElementNS(ns, 'image');
    layer.id = `thinking-${name}`;
    layer.setAttribute('href', `${base}thinking_${name}.png`);
    layer.setAttribute('width', String(WIDTH));
    layer.setAttribute('height', String(HEIGHT));
    svg.append(layer);
    return [name, layer];
  }));
  byId('character').append(svg);
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let active = false;
  let frame = 0;
  let enlarged = false;
  let previousSize = byId('size').value;
  let started = 0;
  let ready = false;
  let lastFrame = '';

  function showFrame(time, motion) {
    const {body, tail, eyes} = frameAt(time, motion);
    const key = `${body}/${tail}/${eyes}`;
    if (key === lastFrame) return;
    lastFrame = key;
    const visible = new Set(['face', `body_${body}`, `tail_${tail}`, `eye_${eyes}`]);
    for (const [name, layer] of Object.entries(layers)) layer.setAttribute('display', visible.has(name) ? 'inline' : 'none');
    svg.dataset.frame = key;
  }

  function animate(time) {
    showFrame(time - started, true);
    frame = requestAnimationFrame(animate);
  }

  function updateThinking() {
    cancelAnimationFrame(frame);
    const moving = active && ready && byId('motion').checked && !reduceMotion.matches && !document.hidden;
    svg.toggleAttribute('hidden', !active);
    byId('pose').hidden = active;
    svg.classList.toggle('float', moving);
    if (moving) {
      started = performance.now();
      showFrame(0, true);
      frame = requestAnimationFrame(animate);
    } else {
      showFrame(0, false);
    }
    byId('thinking-toggle').setAttribute('aria-pressed', String(active));
    byId('thinking-toggle').textContent = active ? 'いつもの顔へ戻す' : '考え顔を試す';
    byId('thinking-status').textContent = active ? 'thinking：体5枚・尻尾2枚・目線2枚＋固定の顔と手' : '';
    byId('artwork-note').textContent = `絵：${active ? 'thinking 原画10枚' : 'Dogido_nomal.png'} ／ 作者制作の原画・反転なし`;
    if (active) byId('mode-label').textContent = '川柳を考え中：動きのプレビュー';
  }

  byId('thinking-toggle').addEventListener('click', () => {
    if (!ready) return;
    active = !active;
    setMode('normal');
    updateThinking();
  });
  byId('thinking-large').addEventListener('click', () => {
    enlarged = !enlarged;
    if (enlarged) previousSize = byId('size').value;
    byId('size').value = enlarged ? '28' : previousSize;
    byId('size').dispatchEvent(new Event('input'));
    byId('thinking-large').setAttribute('aria-pressed', String(enlarged));
    byId('thinking-large').textContent = enlarged ? '元の大きさへ戻す' : '動きを大きく見る';
  });
  byId('motion').addEventListener('input', updateThinking);
  byId('reset').addEventListener('click', () => {
    enlarged = false;
    byId('thinking-large').setAttribute('aria-pressed', 'false');
    byId('thinking-large').textContent = '動きを大きく見る';
    updateThinking();
  });
  reduceMotion.addEventListener('change', updateThinking);
  document.addEventListener('visibilitychange', updateThinking);
  document.addEventListener('preview-mode-change', event => {
    if (event.detail !== 'normal') { active = false; updateThinking(); }
  });
  updateThinking();
  byId('thinking-toggle').disabled = true;
  const preloads = names.map(name => new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      if (img.naturalWidth === WIDTH && img.naturalHeight === HEIGHT) resolve();
      else reject(new Error(`Unexpected canvas: thinking_${name}.png`));
    };
    img.onerror = reject;
    img.src = `${base}thinking_${name}.png`;
  }));
  Promise.all(preloads).then(() => {
    ready = true;
    byId('thinking-toggle').disabled = false;
  }).catch(() => {
    byId('thinking-status').textContent = '考え顔の素材を読み込めませんでした。ページを再読み込みしてください。';
  });
})();
