package dogido.lab;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class IdleMotionTest {
    @Test void hoverDoesNotAccumulateAltitude() {
        double y = 0;
        for (int tick = 1; tick <= 80000; tick++) {
            y += IdleMotion.hoverStep(tick);
            assertTrue(Math.abs(y) <= .060000001);
        }
        assertEquals(0, y, 1e-9);
    }

    @Test void loopClosesWithoutASnapForEveryAxis() {
        for (boolean left : new boolean[]{false, true}) {
            for (int axis = 0; axis < 3; axis++) {
                assertEquals(IdleMotion.angle(left, axis, 0),
                    IdleMotion.angle(left, axis, IdleMotion.LENGTH - .000001F), .0001);
                assertEquals(IdleMotion.angle(left, axis, .625F),
                    IdleMotion.angle(left, axis, IdleMotion.LENGTH * 3 + .625F), .0001);
            }
        }
    }

    @Test void handsFlapInOppositeDirections() {
        assertEquals(Math.toRadians(32.5), IdleMotion.angle(false, 2, .625F), 1e-6);
        assertEquals(Math.toRadians(-32.5), IdleMotion.angle(true, 2, .625F), 1e-6);
    }
}
