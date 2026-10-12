package dogido.fabric.horse_recall;

import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class HorseRecallSafetyTest {
    private static final UUID OWNER = UUID.fromString("00000000-0000-0000-0000-000000000001");
    private static final String DIM = "minecraft:overworld";

    @Test void executionRequiresActualOwnerAndAnUnoccupiedLiveAdultMount() {
        assertNull(check(HorseRecallChecks.Kind.HORSE, true, true, OWNER, true, DIM, false, false, false));
        assertEquals("horse_not_loaded", HorseRecallChecks.targetError(null, OWNER, DIM));
        assertEquals("horse_not_loaded", check(null, true, true, OWNER, true, DIM, false, false, false));
        assertEquals("horse_not_alive", check(HorseRecallChecks.Kind.HORSE, false, true, OWNER, true, DIM, false, false, false));
        assertEquals("horse_not_owned", check(HorseRecallChecks.Kind.HORSE, true, false, OWNER, true, DIM, false, false, false));
        assertEquals("horse_not_owned", check(HorseRecallChecks.Kind.HORSE, true, true, null, true, DIM, false, false, false));
        assertEquals("horse_not_owned", check(HorseRecallChecks.Kind.HORSE, true, true, new UUID(1, 1), true, DIM, false, false, false));
        assertEquals("mount_not_adult", check(HorseRecallChecks.Kind.HORSE, true, true, OWNER, false, DIM, false, false, false));
        assertEquals("dimension_changed", check(HorseRecallChecks.Kind.HORSE, true, true, OWNER, true, "minecraft:the_nether", false, false, false));
        assertEquals("horse_occupied", check(HorseRecallChecks.Kind.HORSE, true, true, OWNER, true, DIM, true, false, false));
        assertEquals("horse_occupied", check(HorseRecallChecks.Kind.HORSE, true, true, OWNER, true, DIM, false, true, false));
        assertEquals("horse_leashed", check(HorseRecallChecks.Kind.HORSE, true, true, OWNER, true, DIM, false, false, true));
    }

    private static String check(HorseRecallChecks.Kind kind, boolean alive, boolean tamed, UUID owner, boolean adult,
            String dim, boolean passengers, boolean vehicle, boolean leashed) {
        return HorseRecallChecks.targetError(new HorseRecallChecks.Target(kind, alive, tamed, owner, "ポチ",
            adult, true, dim, passengers, vehicle, leashed), OWNER, DIM);
    }

    @Test void everySpeciesUsesItsOwnerOrTheApprovedNamedEquipmentRule() {
        for (var kind : HorseRecallChecks.Kind.values()) {
            var owned = new HorseRecallChecks.Target(kind, true, true, OWNER, null, true, false, DIM, false, false, false);
            assertEquals(kind.requiresTaming(), HorseRecallChecks.authorized(owned, OWNER), kind.name());
            var named = new HorseRecallChecks.Target(kind, true, true, null, "ポチ", true, true, DIM, false, false, false);
            assertEquals(kind.permitsNamedEquipment(), HorseRecallChecks.authorized(named, OWNER), kind.name());
            for (var invalid : java.util.List.of(
                    new HorseRecallChecks.Target(kind, true, true, new UUID(1, 1), "ポチ", true, true, DIM, false, false, false),
                    new HorseRecallChecks.Target(kind, true, true, null, null, true, true, DIM, false, false, false),
                    new HorseRecallChecks.Target(kind, true, true, null, "ポチ", true, false, DIM, false, false, false))) {
                assertFalse(HorseRecallChecks.authorized(invalid, OWNER), kind.name());
            }
            assertEquals(kind, HorseRecallChecks.Kind.fromEntityId("minecraft:" + kind.wireName()));
        }
        for (String rejected : java.util.List.of("minecraft:husk", "minecraft:ghast", "minecraft:wolf", "other:pig", "pig"))
            assertNull(HorseRecallChecks.Kind.fromEntityId(rejected));
        assertEquals(14, HorseRecallChecks.Kind.values().length);
    }

    @Test void nautilusNeedsNineWaterCellsAndHappyGhastNeedsItsWholeBodySpace() {
        var point = new HorseRecallLanding.Point(0, 64, 0);
        for (var kind : java.util.List.of(HorseRecallChecks.Kind.NAUTILUS, HorseRecallChecks.Kind.ZOMBIE_NAUTILUS)) {
            var water = HorseRecallLanding.volume(point, kind, 0.875, 0.95);
            var cells = new java.util.HashSet<HorseRecallLanding.Cell>();
            assertTrue(water.all(cell -> { cells.add(cell); return true; }));
            assertEquals(9, cells.size(), "3x3, not a nine-cell line or 9x9");
            assertEquals(3, cells.stream().map(HorseRecallLanding.Cell::x).distinct().count());
            assertEquals(3, cells.stream().map(HorseRecallLanding.Cell::z).distinct().count());
            for (var missing : cells) assertFalse(water.all(cell -> !cell.equals(missing)), "one missing water cell rejects landing");
            assertFalse(water.all(cell -> cell.z() == 0), "a long narrow stream does not satisfy 3x3");
        }
        var air = HorseRecallLanding.volume(point, HorseRecallChecks.Kind.HAPPY_GHAST, 4, 4);
        assertEquals(4, air.maxX() - air.minX() + 1);
        assertEquals(4, air.maxY() - air.minY() + 1);
        assertEquals(4, air.maxZ() - air.minZ() + 1);
        var large = HorseRecallLanding.volume(point, HorseRecallChecks.Kind.HAPPY_GHAST, 5, 5);
        assertTrue(large.minX() <= -2.5 && large.maxX() + 1 >= 2.5);
        assertEquals(5, large.maxY() - large.minY() + 1);
        var camel = HorseRecallLanding.volume(point, HorseRecallChecks.Kind.CAMEL, 1.7, 2.375);
        assertEquals(3, camel.maxY() - camel.minY() + 1);
        assertTrue(HorseRecallChecks.Kind.HORSE.rears());
        assertFalse(HorseRecallChecks.Kind.CAMEL.rears());
        assertFalse(HorseRecallChecks.Kind.LLAMA.rears());
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
