package dogido.fabric;

import java.util.HashSet;
import javax.imageio.ImageIO;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

final class ThinkingAnimationTest {
    @Test void tenAuthoredLayersShareTransparentCanvas() throws Exception {
        for (String part : new String[]{"body_1", "body_2", "body_3", "body_4", "body_5",
                "face", "eye_l", "eye_r", "tail_1", "tail_2"}) {
            try (var input = getClass().getResourceAsStream("/assets/dogido/textures/gui/thinking/thinking_" + part + ".png")) {
                assertNotNull(input, part);
                var image = ImageIO.read(input);
                assertEquals(1210, image.getWidth());
                assertEquals(1400, image.getHeight());
                assertTrue(image.getColorModel().hasAlpha());
                assertEquals(0, image.getRGB(0, 0) >>> 24);
            }
        }
    }
    @Test void independentTimingsHaveKnownBoundaries() {
        assertEquals(new ThinkingAnimation.Frame(1, 1, "r"), ThinkingAnimation.frame(0, true));
        assertEquals(new ThinkingAnimation.Frame(3, 2, "r"), ThinkingAnimation.frame(2250, true));
        assertEquals(new ThinkingAnimation.Frame(3, 2, "r"), ThinkingAnimation.frame(2550, true));
        assertEquals(new ThinkingAnimation.Frame(4, 2, "l"), ThinkingAnimation.frame(5400, true));
        assertEquals(new ThinkingAnimation.Frame(3, 2, "r"), ThinkingAnimation.frame(9600, true));
        assertEquals(new ThinkingAnimation.Frame(2, 2, "l"), ThinkingAnimation.frame(16800, true));
    }
    @Test void bodyRunsForwardAndBackwardThroughOnlyAuthoredDrawings() {
        int[] sequence = {1, 2, 3, 4, 5, 4, 3, 2};
        for (int i = 0; i < sequence.length * 3; i++) {
            assertEquals(sequence[i % sequence.length], ThinkingAnimation.frame(i * 960, true).body());
            assertEquals(sequence[i % sequence.length], ThinkingAnimation.frame((i + 1) * 960 - 1, true).body());
        }
        var latest = ThinkingAnimation.frame(Long.MAX_VALUE, true);
        assertTrue(latest.body() >= 1 && latest.body() <= 5);
        assertTrue(latest.tail() >= 1 && latest.tail() <= 2);
    }
    @Test void gazeHoldsEachDirectionForSeveralSeconds() {
        long[] boundaries = {0, 5400, 9600, 16800, 21600};
        String[] eyes = {"r", "l", "r", "l"};
        for (int i = 0; i < eyes.length; i++) {
            assertEquals(eyes[i], ThinkingAnimation.frame(boundaries[i], true).eyes());
            assertEquals(eyes[i], ThinkingAnimation.frame(boundaries[i + 1] - 1, true).eyes());
        }
        assertEquals("r", ThinkingAnimation.frame(21600, true).eyes());
        assertEquals(7.6, ThinkingAnimation.FLOAT_PERIOD_SECONDS);
    }
    @Test void allTwentyCombinationsKeepTheSameFaceAndLayerOrder() {
        var combinations = new HashSet<ThinkingAnimation.Frame>();
        for (long t = 0; t < 60000; t += 10) {
            var frame = ThinkingAnimation.frame(t, true);
            combinations.add(frame);
            var layers = ThinkingAnimation.layers(frame);
            assertEquals(4, layers.length);
            assertTrue(layers[0].startsWith("thinking_tail_"));
            assertTrue(layers[1].startsWith("thinking_body_"));
            assertEquals("thinking_face.png", layers[2]);
            assertTrue(layers[3].startsWith("thinking_eye_"));
            assertEquals(ThinkingAnimation.frame(0, true), ThinkingAnimation.frame(t, false));
        }
        assertEquals(20, combinations.size());
        assertEquals(ThinkingAnimation.frame(0, true), ThinkingAnimation.frame(-100, true));
    }
    @Test void thinkingUsesSameWidthAndBottomAnchorAsNormal() {
        var normal = CharacterPlacement.approved(1000, 600);
        var thinking = CharacterPlacement.approved(1000, 600, ThinkingAnimation.WIDTH, ThinkingAnimation.HEIGHT);
        assertEquals(normal.width(), thinking.width());
        assertEquals(normal.y() + normal.height(), thinking.y() + thinking.height());
        assertEquals(104, thinking.height());
    }
}
