package dogido.fabric;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

final class CharacterBlinkTest {
    @Test void startsOpenThenBrieflyClosesAndReopens() {
        assertFalse(CharacterBlink.closed(0, true));
        assertFalse(CharacterBlink.closed(4499, true));
        assertTrue(CharacterBlink.closed(4500, true));
        assertTrue(CharacterBlink.closed(4639, true));
        assertFalse(CharacterBlink.closed(4640, true));
    }

    @Test void skippedFramesDoNotLeaveTheEyesClosed() {
        assertTrue(CharacterBlink.closed(4500, true));
        assertFalse(CharacterBlink.closed(10000, true));
        assertTrue(CharacterBlink.closed(4640 * 20L + 4500, true));
    }

    @Test void motionOffAlwaysUsesTheOpenArtwork() {
        for (long elapsed : new long[]{0, 4500, 4639, 4640, Long.MAX_VALUE}) {
            assertFalse(CharacterBlink.closed(elapsed, false));
        }
        assertFalse(CharacterBlink.closed(-1, true));
    }
}
