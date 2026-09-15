# Character placement sample

`character.png` is the author's `animation/Dogido_nomal.png`, copied byte-for-byte.
`character_closed.png` is the author's `animation/Dogido_nomal_close.png`, also unmodified.
Both are 1398 × 1336 RGBA; preserve aspect ratio and transparency. Do not mirror the artwork,
texture coordinates, or drawing geometry. Use authored direction-specific assets.

Replace both through a resource pack under `assets/dogido/textures/gui/` with matching
canvas dimensions and alignment. The character floats vertically and blinks (4500 ms open,
140 ms closed); motion off disables both. No AI-generated symmetry cleanup is used.
Server display state is not connected to this artwork. The workshop scroll is a separate HUD.
