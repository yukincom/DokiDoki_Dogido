// Keep timings identical to Fabric ThinkingAnimation; pure function for tests.
(() => {
  const WIDTH = 1210, HEIGHT = 1400;
  const BODY_STEP_MS = 960;
  // Authored frames only: go back through the drawings instead of morphing 5 into 1.
  const BODY_SEQUENCE = Object.freeze([1, 2, 3, 4, 5, 4, 3, 2]);
  const LAYER_NAMES = Object.freeze(['tail_1', 'tail_2',
    'body_1', 'body_2', 'body_3', 'body_4', 'body_5', 'face', 'eye_r', 'eye_l']);
  const frameAt = (elapsedMs, motion = true) => {
    const t = motion ? Math.max(0, elapsedMs) : 0;
    const gaze = t % 21600;
    return {
      body: BODY_SEQUENCE[Math.floor(t / BODY_STEP_MS) % BODY_SEQUENCE.length],
      tail: Math.floor((t % 6600 + 1050) / 3300) % 2 + 1,
      eyes: gaze < 5400 || (gaze >= 9600 && gaze < 16800) ? 'r' : 'l',
    };
  };
  const api = {frameAt, WIDTH, HEIGHT, BODY_STEP_MS, BODY_SEQUENCE, LAYER_NAMES};
  if (typeof module !== 'undefined') module.exports = api;
  else window.DogidoThinkingAnimation = api;
})();
