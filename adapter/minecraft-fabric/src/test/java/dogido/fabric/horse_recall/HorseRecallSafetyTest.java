package dogido.fabric.horse_recall;

import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class HorseRecallSafetyTest {
    private static final UUID OWNER = UUID.fromString("00000000-0000-0000-0000-000000000001");
    private static final String DIM = "minecraft:overworld";

    @Test void executionRequiresActualOwnerTamingAndAnUnoccupiedLiveNormalHorse() {
        assertNull(check(true, true, true, OWNER, DIM, false, false, false));
        assertEquals("horse_not_loaded", HorseRecallChecks.targetError(null, OWNER, DIM));
        assertEquals("horse_not_loaded", check(false, true, true, OWNER, DIM, false, false, false));
        assertEquals("horse_not_alive", check(true, false, true, OWNER, DIM, false, false, false));
        assertEquals("horse_not_owned", check(true, true, false, OWNER, DIM, false, false, false));
        assertEquals("horse_not_owned", check(true, true, true, null, DIM, false, false, false));
        assertEquals("horse_not_owned", check(true, true, true, new UUID(1, 1), DIM, false, false, false));
        assertEquals("dimension_changed", check(true, true, true, OWNER, "minecraft:the_nether", false, false, false));
        assertEquals("horse_occupied", check(true, true, true, OWNER, DIM, true, false, false));
        assertEquals("horse_occupied", check(true, true, true, OWNER, DIM, false, true, false));
        assertEquals("horse_leashed", check(true, true, true, OWNER, DIM, false, false, true));
    }

    private static String check(boolean normal, boolean alive, boolean tamed, UUID owner, String dim,
            boolean passengers, boolean vehicle, boolean leashed) {
        return HorseRecallChecks.targetError(new HorseRecallChecks.Target(normal, alive, tamed, owner, dim,
            passengers, vehicle, leashed), OWNER, DIM);
    }

    @Test void destinationMustBeSafeAndTwoToFiveBlocksAwayWithLimitedHeight() {
        AtomicInteger checked = new AtomicInteger();
        assertNull(HorseRecallLanding.find(-0.5, 64, -0.5, point -> {
            assertTrue(point.near(-0.5, 64, -0.5));
            checked.incrementAndGet();
            return false;
        }));
        assertTrue(checked.get() > 0 && checked.get() < 400);
        var expected = new HorseRecallLanding.Point(-3, 64, -1);
        assertEquals(expected, HorseRecallLanding.find(-0.5, 64, -0.5, expected::equals));
        assertFalse(expected.near(-3, 64, -1), "player moved into the reserved landing");
        assertFalse(expected.near(20, 64, 20), "player moved away during preview");
        assertFalse(expected.near(-0.5, 68, -0.5));
    }

    @Test void previewCancellationCannotLeadToMovementOrSound() {
        var state = new HorseRecallLifecycle();
        assertTrue(state.startPreview());
        for (int i = 0; i < 9; i++) assertFalse(state.previewTick());
        assertFalse(state.moved());
        state.cancel();
        assertFalse(state.previewTick());
        assertFalse(state.moved());
        assertFalse(state.startPreview());
        assertFalse(state.startBurst());
        assertTrue(state.recordResult());
        assertFalse(state.recordResult());
    }

    @Test void duplicateCallbacksAndPresentationFailureCannotRepeatMovementOrResult() {
        var state = new HorseRecallLifecycle();
        state.startPreview();
        for (int i = 0; i < 10; i++) state.previewTick();
        assertTrue(state.moved());
        assertTrue(state.recordResult());
        assertFalse(state.moved());
        assertTrue(state.startBurst());
        assertFalse(state.startBurst());
        state.cancel();
        assertFalse(state.moved());
        assertFalse(state.startBurst());
        assertFalse(state.recordResult(), "do not overwrite acknowledged movement with a visual failure");
    }

    @Test void sparkleAlphaIsBoundedFlickersAndEndsAtZero() {
        float previous = -1;
        boolean fell = false;
        boolean rose = false;
        for (int age = 0; age <= 30; age++) {
            float alpha = HorseRecallVisuals.alpha(age, 30, 0.2, true);
            assertTrue(alpha >= 0 && alpha <= 1);
            if (previous >= 0) { fell |= alpha < previous; rose |= alpha > previous; }
            previous = alpha;
        }
        assertTrue(fell && rose);
        assertEquals(0, HorseRecallVisuals.alpha(30, 30, 0.2, true));
        assertEquals(0, HorseRecallVisuals.alpha(31, 30, 0.2, false));
    }
}
