package dogido.fabric;

import java.util.Map;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class PortalObservationTrackerTest {
    private static final long POSITION = 11L;
    private static final String PORTAL = "nether_portal";
    private final PortalObservationTracker tracker = new PortalObservationTracker();

    private String observe(Map<Long, String> blocks, boolean near, boolean visible, long tick) {
        return tracker.observe(blocks, near ? Map.of(POSITION, PORTAL) : Map.of(), near ? POSITION : null, visible, tick, 40);
    }
    @Test void changedLoadedBlockAppearsAndSurvivesSameTickAndNextSnapshot() {
        assertNull(observe(Map.of(POSITION, ""), false, true, 0));
        assertEquals("appeared", observe(Map.of(POSITION, PORTAL), true, true, 4));
        assertEquals("appeared", observe(Map.of(POSITION, PORTAL), true, true, 4));
        assertEquals("appeared", observe(Map.of(POSITION, PORTAL), true, true, 20));
    }
    @Test void approachingPreviouslyObservedPortalIsArrival() {
        observe(Map.of(POSITION, PORTAL), false, true, 0);
        assertEquals("arrived", observe(Map.of(POSITION, PORTAL), true, true, 20));
    }
    @Test void firstScanAndPreviouslyUnloadedPositionNeverProveCreation() {
        assertEquals("arrived", observe(Map.of(POSITION, PORTAL), true, true, 0));
        tracker.reset();
        observe(Map.of(), false, true, 0);
        assertEquals("arrived", observe(Map.of(POSITION, PORTAL), true, true, 20));
    }
    @Test void unseenCreationUsesNeutralObservationEvenWhenPlayerLaterTurns() {
        observe(Map.of(POSITION, ""), false, true, 0);
        assertEquals("observed", observe(Map.of(POSITION, PORTAL), true, false, 20));
        assertEquals("observed", observe(Map.of(POSITION, PORTAL), true, true, 40));
    }
    @Test void disappearanceEndsEncounter() {
        observe(Map.of(POSITION, ""), false, true, 0);
        observe(Map.of(POSITION, PORTAL), true, true, 20);
        assertNull(observe(Map.of(POSITION, PORTAL), false, true, 40));
        assertEquals("arrived", observe(Map.of(POSITION, PORTAL), true, true, 60));
    }
    @Test void samePortalBlockChangeKeepsEncounterButSeparateExistingPortalDoesNot() {
        long secondBlock = 12L;
        long otherPortal = 99L;
        tracker.observe(Map.of(POSITION, "", secondBlock, "", otherPortal, PORTAL), Map.of(), null, true, 0, 40);
        Map<Long, String> blocks = Map.of(POSITION, PORTAL, secondBlock, PORTAL, otherPortal, PORTAL);
        assertEquals("appeared", tracker.observe(blocks, blocks, POSITION, true, 4, 40));
        assertEquals("appeared", tracker.observe(blocks, blocks, secondBlock, true, 8, 40));
        assertEquals("arrived", tracker.observe(blocks, Map.of(otherPortal, PORTAL), otherPortal, true, 20, 40));
    }
    @Test void resetOrLongGapDropsBlockChangeEvidence() {
        observe(Map.of(POSITION, ""), false, true, 0);
        tracker.reset();
        assertEquals("arrived", observe(Map.of(POSITION, PORTAL), true, true, 20));
        tracker.reset();
        observe(Map.of(POSITION, ""), false, true, 0);
        assertEquals("arrived", observe(Map.of(POSITION, PORTAL), true, true, 41));
    }
}
