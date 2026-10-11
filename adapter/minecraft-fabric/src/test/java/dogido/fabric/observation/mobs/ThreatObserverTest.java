package dogido.fabric.observation.mobs;

import java.util.List;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class ThreatObserverTest {
    private static final UUID ENEMY_A = UUID.fromString("00000000-0000-0000-0000-00000000000a");
    private static final UUID ENEMY_B = UUID.fromString("00000000-0000-0000-0000-00000000000b");
    private final AtomicLong tick = new AtomicLong(100);
    private final BossTracker bosses = new BossTracker();
    private final ThreatObserver observer = new ThreatObserver(
        new ThreatObserver.Settings(16, 30, 3, 2, 3, 3, 4),
        new MobClassifier(), bosses, () -> {}, tick::get);

    private ThreatObservation threat(UUID id, boolean visible) {
        return threat(id, visible, 10);
    }

    private ThreatObservation threat(UUID id, boolean visible, double distance) {
        return new ThreatObservation(id, "zombie", distance, "front", "south", "same", false,
            false, visible, false, false, 0, 0, 10, 20, false, false, null, null);
    }

    private ThreatObserver rangeObserver() {
        return new ThreatObserver(new ThreatObserver.Settings(16, 16, 3, 2, 3, 3, 100),
            new MobClassifier(), bosses, () -> {}, tick::get);
    }

    private ThreatObserver defaultTimingObserver(double max, double visible) {
        return new ThreatObserver(new ThreatObserver.Settings(max, visible, 3, 7, 6, 16, 100),
            new MobClassifier(), bosses, () -> {}, tick::get);
    }

    @Test void newEnemyUsesInnerBoundaryAndSixConsecutiveTicks() {
        for (double distance : new double[] {15.99, 16.00, 16.01}) {
            tick.set(100);
            ThreatObserver range = defaultTimingObserver(16, 16);
            var input = List.of(threat(ENEMY_A, true, distance));
            assertTrue(range.filterVisibleThreats(input).isEmpty());
            tick.set(105);
            assertTrue(range.filterVisibleThreats(input).isEmpty());
            tick.set(106);
            assertEquals(distance <= 16 ? 1 : 0, range.filterVisibleThreats(input).size());
            tick.set(206);
            assertEquals(distance <= 16 ? 1 : 0, range.filterVisibleThreats(input).size());
        }
    }

    @Test void repeatedBoundaryCrossingsKeepCombatActivePastQuietPeriod() {
        ThreatObserver range = defaultTimingObserver(16, 16);
        range.filterVisibleThreats(List.of(threat(ENEMY_A, true, 16)));
        tick.set(106);
        range.updateCombatTracking(range.filterVisibleThreats(List.of(threat(ENEMY_A, true, 16))), List.of());
        for (long now = 107; now <= 350; now++) {
            tick.set(now);
            var visible = range.filterVisibleThreats(List.of(threat(ENEMY_A, true, now % 2 == 0 ? 15.9 : 16.1)));
            assertEquals(List.of(ENEMY_A), visible.stream().map(ThreatObservation::uuid).toList());
            range.updateCombatTracking(visible, List.of());
            assertTrue(range.combatActive());
            assertFalse(range.shouldSendCombatEnded(visible, List.of(), false));
        }
        tick.set(351);
        assertEquals(1, range.filterVisibleThreats(List.of(threat(ENEMY_A, true, 21))).size());
        assertTrue(range.outcomes().isEmpty(), "range changes are not deaths");
    }

    @Test void defaultOcclusionHoldEndsAfterSixteenTicks() {
        ThreatObserver range = defaultTimingObserver(16, 16);
        assertEquals(1, range.filterVisibleThreats(List.of(threat(ENEMY_A, true, 7))).size());
        tick.set(116);
        assertEquals(1, range.filterVisibleThreats(List.of(threat(ENEMY_A, false, 20))).size());
        tick.set(117);
        assertTrue(range.filterVisibleThreats(List.of(threat(ENEMY_A, false, 20))).isEmpty());
        assertTrue(range.filterVisibleThreats(List.of(threat(ENEMY_A, true, 20))).isEmpty());
    }

    @Test void interruptedConfirmationStartsFromZeroAfterMissingOrOuterObservation() {
        for (boolean outside : new boolean[] {false, true}) {
            tick.set(100);
            ThreatObserver range = defaultTimingObserver(16, 16);
            var inside = List.of(threat(ENEMY_A, true, 16));
            assertTrue(range.filterVisibleThreats(inside).isEmpty());
            tick.set(105);
            assertTrue(range.filterVisibleThreats(inside).isEmpty());
            tick.set(106);
            assertTrue(range.filterVisibleThreats(outside ? List.of(threat(ENEMY_A, true, 16.01)) : List.of()).isEmpty());
            tick.set(107);
            assertTrue(range.filterVisibleThreats(inside).isEmpty());
            tick.set(112);
            assertTrue(range.filterVisibleThreats(inside).isEmpty());
            tick.set(113);
            assertEquals(1, range.filterVisibleThreats(inside).size());
        }
    }

    @Test void rearWarningStillConfirmsWithoutWaiting() {
        // panic距離を2にした既存fixtureで、3ブロックの背後条件だけを検査する。
        ThreatObserver range = rangeObserver();
        ThreatObservation rear = new ThreatObservation(ENEMY_A, "zombie", 3, "back", "north", "same", false,
            true, true, false, false, 0, 0, 3, 20, false, false, null, null);
        assertEquals(1, range.filterVisibleThreats(List.of(rear)).size());
    }

    @Test void newEnemyBeforeQuietDeadlineRestartsTheExistingTimer() {
        ThreatObserver range = defaultTimingObserver(16, 16);
        range.updateCombatTracking(range.filterVisibleThreats(List.of(threat(ENEMY_A, true, 7))), List.of());
        tick.set(101);
        range.updateCombatTracking(range.filterVisibleThreats(List.of()), List.of());
        tick.set(199);
        assertFalse(range.shouldSendCombatEnded(List.of(), List.of(), false));
        var next = range.filterVisibleThreats(List.of(threat(ENEMY_B, true, 7)));
        assertEquals(List.of(ENEMY_B), next.stream().map(ThreatObservation::uuid).toList());
        range.updateCombatTracking(next, List.of());
        tick.set(200);
        range.updateCombatTracking(range.filterVisibleThreats(List.of()), List.of());
        assertFalse(range.shouldSendCombatEnded(List.of(), List.of(), false));
        tick.set(298);
        assertFalse(range.shouldSendCombatEnded(List.of(), List.of(), false));
        tick.set(299);
        assertTrue(range.shouldSendCombatEnded(List.of(), List.of(), false));
        range.endCombat();
        assertFalse(range.shouldSendCombatEnded(List.of(), List.of(), false));
        assertTrue(range.outcomes().isEmpty());
    }

    @Test void configuredAcquisitionDistanceKeepsFiveBlockMargin() {
        for (double[] distances : new double[][] {{24, 16}, {16, 24}}) {
            tick.set(100);
            ThreatObserver range = defaultTimingObserver(distances[0], distances[1]);
            assertTrue(range.filterVisibleThreats(List.of(threat(ENEMY_A, true, 24))).isEmpty());
            tick.set(106);
            assertEquals(1, range.filterVisibleThreats(List.of(threat(ENEMY_A, true, 24))).size());
            tick.set(107);
            assertEquals(1, range.filterVisibleThreats(List.of(threat(ENEMY_A, true, 29))).size());
            tick.set(108);
            assertTrue(range.filterVisibleThreats(List.of(threat(ENEMY_A, true, 29.01))).isEmpty());
            tick.set(109);
            assertTrue(range.filterVisibleThreats(List.of(threat(ENEMY_A, true, 24.01))).isEmpty());
        }
    }

    @Test void confirmedIndividualStaysUntilOuterBoundaryAndReacquiresInside() {
        ThreatObserver range = rangeObserver();
        UUID id = UUID.randomUUID();
        assertTrue(range.filterVisibleThreats(List.of(threat(id, true, 16))).isEmpty());
        tick.set(103);
        assertEquals(1, range.filterVisibleThreats(List.of(threat(id, true, 16))).size());
        tick.set(104);
        assertEquals(1, range.filterVisibleThreats(List.of(threat(id, true, 16.01))).size());
        tick.set(105);
        assertEquals(1, range.filterVisibleThreats(List.of(threat(id, true, 21))).size());
        tick.set(106);
        assertTrue(range.filterVisibleThreats(List.of(threat(id, true, 21.01))).isEmpty());
        tick.set(107);
        assertTrue(range.filterVisibleThreats(List.of(threat(id, true, 20))).isEmpty());
        tick.set(108);
        assertTrue(range.filterVisibleThreats(List.of(threat(id, true, 16))).isEmpty());
        tick.set(111);
        assertEquals(1, range.filterVisibleThreats(List.of(threat(id, true, 16))).size());
    }

    @Test void outerUnknownOfSameSpeciesDoesNotBorrowTrackingOrTriggerClusterConfirmation() {
        ThreatObserver range = rangeObserver();
        UUID known = UUID.randomUUID(), other = UUID.randomUUID();
        List<ThreatObservation> first = List.of(threat(known, true, 16), threat(other, true, 17));
        assertTrue(range.filterVisibleThreats(first).isEmpty());
        tick.set(103);
        assertEquals(List.of(known), range.filterVisibleThreats(first).stream().map(ThreatObservation::uuid).toList());
        tick.set(104);
        assertEquals(List.of(known), range.filterVisibleThreats(List.of(
            threat(known, true, 20), threat(other, true, 20))).stream().map(ThreatObservation::uuid).toList());
    }

    @Test void outerTrackingDoesNotExtendOcclusionHold() {
        ThreatObserver range = rangeObserver();
        UUID id = UUID.randomUUID();
        range.filterVisibleThreats(List.of(threat(id, true, 16)));
        tick.set(103);
        assertEquals(1, range.filterVisibleThreats(List.of(threat(id, true, 16))).size());
        tick.set(106);
        assertEquals(1, range.filterVisibleThreats(List.of(threat(id, false, 20))).size());
        tick.set(107);
        assertTrue(range.filterVisibleThreats(List.of(threat(id, false, 20))).isEmpty());
        tick.set(108);
        assertTrue(range.filterVisibleThreats(List.of(threat(id, true, 20))).isEmpty());
    }

    @Test void missingOrResetIndividualMustBeFoundInsideAgain() {
        ThreatObserver range = rangeObserver();
        UUID id = UUID.randomUUID();
        assertEquals(1, range.filterVisibleThreats(List.of(threat(id, true, 2))).size());
        assertTrue(range.filterVisibleThreats(List.of()).isEmpty());
        assertTrue(range.filterVisibleThreats(List.of(threat(id, true, 20))).isEmpty());
        assertEquals(1, range.filterVisibleThreats(List.of(threat(id, true, 2))).size());
        range.resetTracking();
        assertTrue(range.filterVisibleThreats(List.of(threat(id, true, 20))).isEmpty());
    }

    @Test void nearDangerAndInnerClusterStillConfirmImmediately() {
        ThreatObserver range = rangeObserver();
        assertEquals(1, range.filterVisibleThreats(List.of(threat(UUID.randomUUID(), true, 2))).size());
        assertEquals(2, range.filterVisibleThreats(List.of(
            threat(UUID.randomUUID(), true, 15), threat(UUID.randomUUID(), true, 16))).size());
    }

    @Test void quietPeriodStartsAfterRetainedOuterEnemyLeaves() {
        ThreatObserver range = rangeObserver();
        UUID id = UUID.randomUUID();
        range.updateCombatTracking(range.filterVisibleThreats(List.of(threat(id, true, 2))), List.of());
        tick.set(200);
        List<ThreatObservation> retained = range.filterVisibleThreats(List.of(threat(id, true, 21)));
        range.updateCombatTracking(retained, List.of());
        assertFalse(range.shouldSendCombatEnded(retained, List.of(), false));
        tick.set(201);
        range.updateCombatTracking(range.filterVisibleThreats(List.of()), List.of());
        tick.set(299);
        assertFalse(range.shouldSendCombatEnded(List.of(), List.of(), false));
        tick.set(300);
        assertTrue(range.shouldSendCombatEnded(List.of(), List.of(), false));
    }

    @Test void singleVisibleThreatMustConfirmAndThenHasBoundedOcclusionHold() {
        UUID id = UUID.randomUUID();
        assertTrue(observer.filterVisibleThreats(List.of(threat(id, true))).isEmpty());
        tick.set(102);
        assertTrue(observer.filterVisibleThreats(List.of(threat(id, true))).isEmpty());
        tick.set(103);
        assertEquals(1, observer.filterVisibleThreats(List.of(threat(id, true))).size());
        tick.set(106);
        assertEquals(1, observer.filterVisibleThreats(List.of(threat(id, false))).size());
        tick.set(107);
        assertTrue(observer.filterVisibleThreats(List.of(threat(id, false))).isEmpty());
    }

    @Test void visibleClusterIsImmediateAndResetClearsItsHold() {
        UUID first = UUID.randomUUID(), second = UUID.randomUUID();
        assertEquals(2, observer.filterVisibleThreats(List.of(threat(first, true), threat(second, true))).size());
        observer.resetTracking();
        assertTrue(observer.filterVisibleThreats(List.of(threat(first, false))).isEmpty());
    }

    @Test void outcomeDeduplicationAndBossUpdateShareOneRecord() {
        UUID id = UUID.randomUUID();
        bosses.observeWarden(90, 1, 2, 3, 42);
        assertTrue(observer.recordHostileOutcome(id, "warden", "player_kill", "server_death_event"));
        tick.set(101);
        assertFalse(observer.recordHostileOutcome(id, "warden", "other_death", "client_death_state"));
        assertEquals(1, observer.outcomes().size());
        assertEquals("player_kill", observer.outcomes().getFirst().outcome());
        assertEquals(100, bosses.lastWardenDefeatObservedTick());
        assertEquals(-1, bosses.trackedWardenEntityId());
        assertThrows(UnsupportedOperationException.class, () -> observer.outcomes().clear());
        observer.clearCombatOutcomeObservations();
        assertTrue(observer.outcomes().isEmpty());
    }

    @Test void quietBoundaryEndsCombatOnlyAfterTheLastSignal() {
        observer.updateCombatTracking(List.of(threat(UUID.randomUUID(), true)), List.of());
        assertTrue(observer.combatActive());
        tick.set(103);
        assertFalse(observer.shouldSendCombatEnded(List.of(), List.of(), false));
        tick.set(104);
        assertTrue(observer.shouldSendCombatEnded(List.of(), List.of(), false));
        assertFalse(observer.shouldSendCombatEnded(List.of(), List.of(), true));
        observer.endCombat();
        assertFalse(observer.shouldSendCombatEnded(List.of(), List.of(), false));
    }

    @Test void bossResetKeepsCoordinatesButClearsTrackedIdentityAndEvidence() {
        bosses.observeWarden(80, 12, 34, 56, 99);
        bosses.recordEndCrystalSetup(81);
        bosses.recordTntSetup(82);
        bosses.observeDragon(83, 100);
        bosses.recordDragonDefeat(84);
        bosses.reset();
        assertEquals(-1000, bosses.lastWardenSeenTick());
        assertEquals(-1, bosses.trackedWardenEntityId());
        assertEquals(-1000, bosses.lastDragonDefeatObservedTick());
        assertEquals(-1000, bosses.lastWardenEndCrystalObservedTick());
        assertEquals(-1000, bosses.lastWardenTntSetupObservedTick());
        assertEquals(12, bosses.lastWardenSeenX());
        assertEquals(34, bosses.lastWardenSeenY());
        assertEquals(56, bosses.lastWardenSeenZ());
    }
}
