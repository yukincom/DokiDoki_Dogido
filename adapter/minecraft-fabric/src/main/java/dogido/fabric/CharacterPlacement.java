package dogido.fabric;

/** Pure geometry in Minecraft's GUI-scaled pixels. Keeps the PNG within a resized viewport. */
final class CharacterPlacement {
    static final int TEXTURE_WIDTH = 1398;
    static final int TEXTURE_HEIGHT = 1336;
    record Bounds(int x, int y, int width, int height) { }

    static Bounds lowerLeft(int screenWidth, int screenHeight, int width, int left, int bottom) {
        Bounds right = fit(screenWidth, screenHeight, width, left, bottom);
        return new Bounds(Math.max(1, screenWidth) - right.x() - right.width(),
            right.y(), right.width(), right.height());
    }

    static Bounds approved(int screenWidth, int screenHeight) {
        return lowerLeft(screenWidth, screenHeight, Math.round(screenWidth * .09f),
            Math.round(screenWidth * .025f), Math.round(screenHeight * .04f));
    }

    static Bounds fit(int screenWidth, int screenHeight, int requestedWidth, int right, int bottom) {
        int sw = Math.max(1, screenWidth);
        int sh = Math.max(1, screenHeight);
        int width = Math.max(1, Math.min(Math.min(requestedWidth, sw), (int) Math.floor(sh * (double) TEXTURE_WIDTH / TEXTURE_HEIGHT)));
        int height = Math.max(1, Math.min(sh, (int) Math.round(width * (double) TEXTURE_HEIGHT / TEXTURE_WIDTH)));
        return new Bounds(Math.clamp(sw - width - right, 0, sw - width),
            Math.clamp(sh - height - bottom, 0, sh - height), width, height);
    }
}
