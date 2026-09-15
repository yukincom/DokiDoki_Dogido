package dogido.fabric;

/** Client-only two-frame blink, independent of server and render frame rate. */
final class CharacterBlink {
    static final long OPEN_MS = 4500;
    static final long CLOSED_MS = 140;

    private CharacterBlink() { }

    static boolean closed(long elapsedMs, boolean motion) {
        return motion && elapsedMs >= 0 && elapsedMs % (OPEN_MS + CLOSED_MS) >= OPEN_MS;
    }
}
