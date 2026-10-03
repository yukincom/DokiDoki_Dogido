const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const {frameAt, WIDTH, HEIGHT, BODY_STEP_MS, BODY_SEQUENCE, LAYER_NAMES} = require('./thinking-animation.js');

test('same independent timeline as the game renderer', () => {
  for (const [t, body, tail, eyes] of [[0,1,1,'r'],[2250,3,2,'r'],[2550,3,2,'r'],
    [5400,4,2,'l'],[9600,3,2,'r'],[16800,2,2,'l']]) {
    assert.deepEqual(frameAt(t), {body, tail, eyes});
  }
});
test('five body drawings run forward and backward with no generated in-betweens', () => {
  assert.equal(BODY_STEP_MS, 960);
  assert.deepEqual(BODY_SEQUENCE, [1,2,3,4,5,4,3,2]);
  for (let i = 0; i < BODY_SEQUENCE.length * 3; i++) {
    assert.equal(frameAt(i * BODY_STEP_MS).body, BODY_SEQUENCE[i % 8]);
    assert.equal(frameAt((i + 1) * BODY_STEP_MS - 1).body, BODY_SEQUENCE[i % 8]);
  }
});
test('gaze holds each direction for 4.2 to 7.2 seconds', () => {
  for (const [start,end,eyes] of [[0,5400,'r'],[5400,9600,'l'],[9600,16800,'r'],[16800,21600,'l']]) {
    assert.equal(frameAt(start).eyes, eyes);
    assert.equal(frameAt(end - 1).eyes, eyes);
  }
  assert.equal(frameAt(21600).eyes, 'r');
});
test('all twenty combinations and motion off stays on the first authored pose', () => {
  const combinations = new Set();
  for (let t = 0; t < 60000; t += 10) {
    combinations.add(JSON.stringify(frameAt(t)));
    assert.deepEqual(frameAt(t, false), {body:1, tail:1, eyes:'r'});
  }
  assert.equal(combinations.size, 20);
  assert.deepEqual(frameAt(-100), frameAt(0));
});
test('all ten layers exist with the same RGBA canvas and fixed stacking order', () => {
  assert.deepEqual(LAYER_NAMES, ['tail_1','tail_2','body_1','body_2','body_3','body_4','body_5','face','eye_r','eye_l']);
  for (const name of LAYER_NAMES) {
    const png = readFileSync(join(__dirname, '../../adapter/minecraft-fabric/src/main/resources/assets/dogido/textures/gui/thinking', `thinking_${name}.png`));
    assert.equal(png.subarray(1, 4).toString(), 'PNG');
    assert.equal(png.readUInt32BE(16), WIDTH, name);
    assert.equal(png.readUInt32BE(20), HEIGHT, name);
    assert.equal(png[25], 6, `${name}: RGBA`);
  }
});
