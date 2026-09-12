package dogido.fabric;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

final class CharacterPlacementTest {
    @Test void defaultPlacementKeepsRequestedMarginsAndAspectRatio() {
        var bounds = CharacterPlacement.fit(640, 360, 96, 12, 36);
        assertEquals(new CharacterPlacement.Bounds(532, 234, 96, 90), bounds);
    }

    @Test void resizingAndOversizedOffsetsCannotMoveCharacterOffscreen() {
        for (int[] viewport : new int[][]{{320, 180}, {80, 60}, {1, 1}, {0, 0}}) {
            var b = CharacterPlacement.fit(viewport[0], viewport[1], 256, 2048, 2048);
            assertTrue(b.x() >= 0 && b.y() >= 0);
            assertTrue(b.width() > 0 && b.height() > 0);
            assertTrue(b.x() + b.width() <= Math.max(1, viewport[0]));
            assertTrue(b.y() + b.height() <= Math.max(1, viewport[1]));
        }
    }
}
