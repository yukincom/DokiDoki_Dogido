package dogido.fabric;

import javax.imageio.ImageIO;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

final class CharacterPlacementTest {
    @Test void approvedPlacementUsesViewportPercentagesOnLowerLeft() {
        assertEquals(new CharacterPlacement.Bounds(16, 291, 58, 55), CharacterPlacement.approved(640, 360));
        assertEquals(new CharacterPlacement.Bounds(25, 490, 90, 86), CharacterPlacement.approved(1000, 600));
    }
    @Test void defaultPlacementKeepsRequestedMarginsAndAspectRatio() {
        var bounds = CharacterPlacement.fit(640, 360, 96, 12, 36);
        assertEquals(new CharacterPlacement.Bounds(532, 232, 96, 92), bounds);
    }

    @Test void packagedArtworkMatchesItsDeclaredCanvasAndKeepsAlpha() throws Exception {
        for (String filename : new String[]{"character.png", "character_closed.png"}) {
            try (var input = getClass().getResourceAsStream("/assets/dogido/textures/gui/" + filename)) {
                assertNotNull(input);
                var artwork = ImageIO.read(input);
                assertNotNull(artwork);
                assertEquals(CharacterPlacement.TEXTURE_WIDTH, artwork.getWidth());
                assertEquals(CharacterPlacement.TEXTURE_HEIGHT, artwork.getHeight());
                assertTrue(artwork.getColorModel().hasAlpha());
            }
        }
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
