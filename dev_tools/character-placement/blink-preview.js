// Match Fabric CharacterBlink: 4.5 seconds open, 140 ms closed. Swap only
// authored, canvas-aligned textures; never interpolate, mirror or reshape eyes.
(() => {
  const pose = document.getElementById('pose');
  const character = document.getElementById('character');
  const motion = document.getElementById('motion');
  const openSource = pose.getAttribute('src');
  const closedSource = new URL('character_closed.png', new URL(openSource, document.baseURI)).href;
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  const preload = new Image();
  let ready = false;
  let frame = 0;
  let started = 0;
  let closed = false;

  function show(isClosed) {
    if (closed === isClosed) return;
    closed = isClosed;
    pose.src = closed ? closedSource : openSource;
  }

  function tick(now) {
    show((now - started) % 4640 >= 4500);
    frame = requestAnimationFrame(tick);
  }

  function sync() {
    cancelAnimationFrame(frame);
    show(false);
    // A thinking pose never borrows the normal pose's closed-eye silhouette.
    if (!ready || !motion.checked || reduced.matches || document.hidden || pose.hidden || character.hidden) return;
    started = performance.now();
    frame = requestAnimationFrame(tick);
  }

  const visibility = new MutationObserver(sync);
  visibility.observe(pose, {attributes: true, attributeFilter: ['hidden']});
  visibility.observe(character, {attributes: true, attributeFilter: ['hidden']});
  motion.addEventListener('input', sync);
  document.getElementById('reset').addEventListener('click', sync);
  reduced.addEventListener('change', sync);
  document.addEventListener('visibilitychange', sync);
  preload.onload = () => { ready = true; sync(); };
  // Leave the open artwork intact if the closed texture cannot be loaded.
  preload.onerror = () => { ready = false; sync(); };
  preload.src = closedSource;
})();
