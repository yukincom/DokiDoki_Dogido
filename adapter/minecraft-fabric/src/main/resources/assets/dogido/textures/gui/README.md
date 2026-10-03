# Character placement sample

`character.png` is the author's `animation/Dogido_nomal.png`, copied byte-for-byte.
`character_closed.png` is the author's `animation/Dogido_nomal_close.png`, also unmodified.
Both are 1398 × 1336 RGBA; preserve aspect ratio and transparency. Do not mirror the artwork,
texture coordinates, or drawing geometry. Use authored direction-specific assets.

Replace both through a resource pack under `assets/dogido/textures/gui/` with matching
canvas dimensions and alignment. The character floats vertically and blinks (4500 ms open,
140 ms closed); motion off disables both. No AI-generated symmetry cleanup is used.
`thinking/` contains ten unmodified author PNGs from `animation/thinking/` (1210 × 1400 RGBA).
Body has five drawings, played 1 → 2 → 3 → 4 → 5 → 4 → 3 → 2 at 960 ms per frame.
Thinking floats over 7.6 s; tail swaps every 3.3 s; gaze holds each side for 4.2–7.2 s.
Tail has two frames; face/hands are a fixed layer; pupils have left/right frames.
Draw tail → body → face → pupils at identical bounds. `ThinkingAnimation` changes these layers
independently; never morph, mirror, trim or offset the layers. Motion off uses body 1/tail 1/right.
Actual poem generation selects these via the read-only `character_state` snapshot. No generated
closed-eye draft is used. Completion/failure returns to normal before speech enqueue.
`character_thinking.png` is the preserved earlier single-image prototype, no longer rendered.
The workshop scroll is a separate HUD; its open lifetime does not select the thinking pose.
