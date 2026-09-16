// Authored texture stays byte-for-byte intact. The overlay protects ALL facial
// features and both hands from displacement. Fixed artwork overlaps the moving
// layer by more than the maximum displacement (32 source pixels per axis).
(() => {
  const byId = id => document.getElementById(id);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 1210 1249');
  svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', '川柳を考えるドギド。外周だけがゆっくりもこもこ動く');
  svg.id = 'thinking-pose';
  svg.setAttribute('hidden', '');
  // Only original artwork is composited: no repainting, symmetry or mirroring.
  // Clip boundaries run through flat body color, away from the outer ink line.
  svg.innerHTML = `
    <defs>
      <filter id="thinking-outline" x="-3%" y="-3%" width="106%" height="106%" color-interpolation-filters="sRGB">
        <feTurbulence type="fractalNoise" baseFrequency="0.0025 0.0035" numOctaves="1" seed="8" result="soft-noise"/>
        <feComponentTransfer in="soft-noise" result="visible-noise">
          <feFuncR type="linear" slope="2" intercept="-0.5"/>
          <feFuncG type="linear" slope="2" intercept="-0.5"/>
        </feComponentTransfer>
        <feDisplacementMap in="SourceGraphic" in2="visible-noise" scale="64" xChannelSelector="R" yChannelSelector="G"/>
      </filter>
      <clipPath id="thinking-fixed-interior" clipPathUnits="userSpaceOnUse">
        <path d="M310 70 L815 80 L885 275 L935 445 L940 890 L130 900 L110 590 L180 400 L265 275 Z"/>
        <rect x="880" y="1000" width="350" height="270"/>
      </clipPath>
    </defs>
    <image id="thinking-edge" href="thinking2.png" width="1210" height="1249"/>
    <image href="thinking2.png" width="1210" height="1249" clip-path="url(#thinking-fixed-interior)"/>
  `;
  byId('character').append(svg);
  const noise = svg.querySelector('feTurbulence');
  const edge = svg.querySelector('#thinking-edge');
  const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)');
  let active = false;
  let frame = 0;
  let enlarged = false;
  let previousSize = byId('size').value;

  function animate(time) {
    // Continuous, deterministic drift; no random per-frame jumps or blinking.
    const wave = Math.sin(time / 1000 * 2 * Math.PI / 5.2);
    noise.setAttribute('baseFrequency', `${0.0025 + wave * 0.0011} ${0.0035 + wave * 0.0015}`);
    frame = requestAnimationFrame(animate);
  }

  function updateThinking() {
    cancelAnimationFrame(frame);
    const moving = active && byId('motion').checked && !reduceMotion.matches && !document.hidden;
    svg.toggleAttribute('hidden', !active);
    byId('pose').hidden = active;
    svg.classList.toggle('float', moving);
    if (moving) {
      edge.setAttribute('filter', 'url(#thinking-outline)');
      frame = requestAnimationFrame(animate);
    } else {
      edge.removeAttribute('filter');
    }
    byId('thinking-toggle').setAttribute('aria-pressed', String(active));
    byId('thinking-toggle').textContent = active ? 'いつもの顔へ戻す' : '考え顔を試す';
    byId('thinking-status').textContent = active ? 'thinking2.png：作者の原画・反転なし' : '';
    byId('artwork-note').textContent = `絵：${active ? 'thinking2.png' : 'Dogido_nomal.png'} ／ 作者制作の原画・反転なし`;
    if (active) byId('mode-label').textContent = '川柳を考え中：動きのプレビュー';
  }

  byId('thinking-toggle').addEventListener('click', () => {
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
})();
