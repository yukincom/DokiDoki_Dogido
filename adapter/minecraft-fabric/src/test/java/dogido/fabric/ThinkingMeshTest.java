package dogido.fabric;

import javax.imageio.ImageIO;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

final class ThinkingMeshTest {
    @Test void originalThinkingArtworkHasCorrectCanvasAndTransparency() throws Exception {
        try (var input = getClass().getResourceAsStream("/assets/dogido/textures/gui/character_thinking.png")) {
            assertNotNull(input);
            var image = ImageIO.read(input);
            assertEquals(ThinkingMesh.WIDTH, image.getWidth());
            assertEquals(ThinkingMesh.HEIGHT, image.getHeight());
            assertTrue(image.getColorModel().hasAlpha());
        }
        var normal = CharacterPlacement.approved(1000, 600);
        var thinking = CharacterPlacement.approved(1000, 600, ThinkingMesh.WIDTH, ThinkingMesh.HEIGHT);
        assertEquals(normal.width(), thinking.width());
        assertEquals(normal.y() + normal.height(), thinking.y() + thinking.height());
        assertEquals(93, thinking.height());
    }
    @Test void eyesBrowsCheeksMouthHandsAndSmallBubblesAreProtected() {
        for (float[] p : new float[][]{{400,320},{720,380},{330,150},{730,180},
                {270,465},{830,510},{510,600},{170,630},{340,560},{250,800},
                {650,640},{785,830},{870,660},{1000,1110},{1115,1140}}) {
            assertEquals(0, ThinkingMesh.weight(p[0], p[1]), "protected authored feature");
        }
        for (int row = 0; row <= ThinkingMesh.ROWS; row++) for (int col = 0; col <= ThinkingMesh.COLUMNS; col++) {
            var rest = ThinkingMesh.point(col, row, 0, false);
            if (ThinkingMesh.weight(rest.x(), rest.y()) == 0) {
                for (long t : new long[]{0,650,1300,2600,3900}) assertEquals(rest, ThinkingMesh.point(col, row, t, true));
            }
        }
    }
    @Test void edgesMoveSmoothlyWithinBoundsAndLoopWithoutMirroring() {
        assertNotEquals(ThinkingMesh.point(0, 20, 0, true), ThinkingMesh.point(0, 20, 1300, true));
        for (int row = 0; row <= ThinkingMesh.ROWS; row++) for (int col = 0; col <= ThinkingMesh.COLUMNS; col++) {
            var rest = ThinkingMesh.point(col, row, 0, false);
            for (long time = 0; time < 5200; time += 130) {
                var point = ThinkingMesh.point(col, row, time, true);
                assertTrue(Math.abs(point.x() - rest.x()) <= 32.001);
                assertTrue(Math.abs(point.y() - rest.y()) <= 32.001);
                assertEquals(rest.u(), point.u()); assertEquals(rest.v(), point.v());
                assertEquals(point, ThinkingMesh.point(col, row, time + 5200, true));
            }
        }
    }

    @Test void movingQuadsNeverFoldOrReverseWinding() {
        for (long time = 0; time < 5200; time += 130) {
            for (int row = 0; row < ThinkingMesh.ROWS; row++) for (int col = 0; col < ThinkingMesh.COLUMNS; col++) {
                var a = ThinkingMesh.point(col, row, time, true);
                var b = ThinkingMesh.point(col + 1, row, time, true);
                var c = ThinkingMesh.point(col + 1, row + 1, time, true);
                var d = ThinkingMesh.point(col, row + 1, time, true);
                assertTrue(cross(a, b, c) > 0);
                assertTrue(cross(a, c, d) > 0);
            }
        }
    }
    private static float cross(ThinkingMesh.Point a, ThinkingMesh.Point b, ThinkingMesh.Point c) {
        return (b.x() - a.x()) * (c.y() - a.y()) - (b.y() - a.y()) * (c.x() - a.x());
    }
}
