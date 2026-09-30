package dogido.fabric;

import static org.junit.jupiter.api.Assertions.*;

import java.util.Map;
import java.util.UUID;
import org.junit.jupiter.api.Test;

final class SoundObservationPolicyTest {
    private static final UUID SOURCE = UUID.fromString("00000000-0000-0000-0000-000000000001");
    private static final SoundObservationPolicy.Point LISTENER = new SoundObservationPolicy.Point(0, 0, 0);
    private static final SoundObservationPolicy.Point OLD = new SoundObservationPolicy.Point(3, 0, 0);

    @Test
    void followsOnlyTheHeardEntityAndDropsItWhenItLeavesHearingRange() {
        var current = new SoundObservationPolicy.Point(0, 0, 11);
        assertEquals(current, SoundObservationPolicy.retainedPosition(SOURCE, OLD, Map.of(SOURCE, current), LISTENER, 12));
        assertNull(SoundObservationPolicy.retainedPosition(SOURCE, OLD,
            Map.of(SOURCE, new SoundObservationPolicy.Point(13, 0, 0)), LISTENER, 12));
        assertNull(SoundObservationPolicy.retainedPosition(SOURCE, OLD, Map.of(), LISTENER, 12));
        assertNull(SoundObservationPolicy.retainedPosition(SOURCE, OLD,
            Map.of(UUID.randomUUID(), OLD), LISTENER, 12));
    }

    @Test
    void unknownEntityUsesOriginalCoordinatesAndTheExistingRange() {
        assertEquals(OLD, SoundObservationPolicy.retainedPosition(null, OLD, Map.of(), LISTENER, 12));
        assertNull(SoundObservationPolicy.retainedPosition(null, OLD, Map.of(),
            new SoundObservationPolicy.Point(16, 0, 0), 12));
        assertNull(SoundObservationPolicy.entityUuid("pos:entity.enderman.teleport:1:2:3"));
        assertEquals(SOURCE, SoundObservationPolicy.entityUuid(SOURCE.toString()));
    }

    @Test
    void nearbyDifferentSpeciesCannotOwnTheSound() {
        assertFalse(SoundObservationPolicy.matchesMobSound("enderman", "zombie"));
        assertFalse(SoundObservationPolicy.matchesMobSound("zombie", "skeleton"));
        assertFalse(SoundObservationPolicy.matchesMobSound("zombie", "zombified_piglin"));
        assertTrue(SoundObservationPolicy.matchesMobSound("enderman", "enderman"));
        assertTrue(SoundObservationPolicy.matchesMobSound("spider", "cave_spider"));
        assertTrue(SoundObservationPolicy.matchesMobSound("creeper", "charged_creeper"));
    }

    @Test
    void hostileSoundCategoryDoesNotProveNeutralMobHostility() {
        for (String kind : SoundObservationPolicy.NEUTRAL_MONSTER_TYPES) {
            assertFalse(SoundObservationPolicy.unresolvedSoundIsThreat(kind));
        }
        assertTrue(SoundObservationPolicy.unresolvedSoundIsThreat("zombie"));
        assertTrue(SoundObservationPolicy.unresolvedSoundIsThreat("skeleton"));
    }

    @Test
    void soundAgeMeasuresRealElapsedTimeWithoutRefreshOnRead() {
        long heardAt = 1_000_000_000L;
        assertEquals(0, SoundObservationPolicy.ageMillis(heardAt, heardAt));
        assertEquals(7_500, SoundObservationPolicy.ageMillis(8_500_000_000L, heardAt));
        assertEquals(15_000, SoundObservationPolicy.ageMillis(16_000_000_000L, heardAt));
    }
}
