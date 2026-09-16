# Character placement sample

`character.png` is the author's `animation/Dogido_nomal.png`, copied byte-for-byte.
`character_closed.png` is the author's `animation/Dogido_nomal_close.png`, also unmodified.
Both are 1398 × 1336 RGBA; preserve aspect ratio and transparency. Do not mirror the artwork,
texture coordinates, or drawing geometry. Use authored direction-specific assets.

Replace both through a resource pack under `assets/dogido/textures/gui/` with matching
canvas dimensions and alignment. The character floats vertically and blinks (4500 ms open,
140 ms closed); motion off disables both. No AI-generated symmetry cleanup is used.
`character_thinking.png` is the author's unmodified `animation/thinking2.png` (1210 × 1249 RGBA).
Actual poem generation selects it via the read-only `character_state` snapshot. A connected
GUI mesh moves only the outline, protecting face, hands and small bubbles. No generated
closed-eye draft is used. Completion/failure returns to normal before speech enqueue.
The workshop scroll is a separate HUD; its open lifetime does not select the thinking pose.
