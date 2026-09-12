package dogido.fabric;

/** Pure geometry in Minecraft's GUI-scaled pixels. Keeps the PNG within a resized viewport. */
final class CharacterPlacement {
    record Bounds(int x, int y, int width, int height) { }

    static Bounds fit(int screenWidth, int screenHeight, int requestedWidth, int right, int bottom) {
        int sw = Math.max(1, screenWidth);
        int sh = Math.max(1, screenHeight);
        int width = Math.max(1, Math.min(Math.min(requestedWidth, sw), (int) Math.floor(sh * 728.0 / 680)));
        int height = Math.max(1, Math.min(sh, (int) Math.round(width * 680.0 / 728)));
        return new Bounds(Math.clamp(sw - width - right, 0, sw - width),
            Math.clamp(sh - height - bottom, 0, sh - height), width, height);
    }
}
