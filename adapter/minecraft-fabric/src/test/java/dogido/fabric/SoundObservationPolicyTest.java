package dogido.fabric;

import static org.junit.jupiter.api.Assertions.*;

import java.util.Map;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;

final class SoundObservationPolicyTest {
    private static final UUID SOURCE = UUID.fromString("00000000-0000-0000-0000-000000000001");
    private static final SoundObservationPolicy.Point LISTENER = new SoundObservationPolicy.Point(0, 0, 0);
    private static final SoundObservationPolicy.Point OLD = new SoundObservationPolicy.Point(3, 0, 0);

    @Test
    void sculkWarningsRequireActivationRatherThanAnyBlockSound() {
        assertEquals("sculk_sensor", SoundObservationPolicy.sculkActivationKind("block.sculk_sensor.clicking"));
        assertEquals("sculk_shrieker", SoundObservationPolicy.sculkActivationKind("minecraft:block.sculk_shrieker.shriek"));
        for (String id : List.of("block.sculk_sensor.clicking_stop", "block.sculk_sensor.place", "block.sculk_sensor.break",
                "block.sculk_sensor.step", "block.sculk_shrieker.place", "block.sculk_shrieker.break", "entity.warden.heartbeat")) {
            assertNull(SoundObservationPolicy.sculkActivationKind(id), id);
        }
        assertNull(SoundObservationPolicy.sculkActivationKind(null));
    }

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

    @Test
    void petNameNeedsAnUnambiguousSameSpeciesSoundSource() {
        var cat = new SoundObservationPolicy.MobSource(SOURCE, "cat", new SoundObservationPolicy.Point(0.125, 0, 0));
        var other = UUID.fromString("00000000-0000-0000-0000-000000000002");
        var wolf = new SoundObservationPolicy.MobSource(other, "wolf", LISTENER);
        assertEquals(SOURCE, SoundObservationPolicy.uniqueAmbientSource("cat", LISTENER, List.of(wolf, cat)));
        assertNull(SoundObservationPolicy.uniqueAmbientSource("cat", LISTENER, List.of(wolf)));
        assertNull(SoundObservationPolicy.uniqueAmbientSource("cat", LISTENER, List.of(cat,
            new SoundObservationPolicy.MobSource(other, "cat", LISTENER))));
        assertNull(SoundObservationPolicy.uniqueAmbientSource("cat", OLD, List.of(cat)));
        assertNull(SoundObservationPolicy.uniqueAmbientSource(null, LISTENER, List.of(cat)));
    }

    @Test
    void imitationAndOverlappingSpeciesKeepTheActualEmitter() {
        assertEquals("parrot", SoundObservationPolicy.mobSoundEmitter("minecraft:entity.parrot.imitate.creeper"));
        assertEquals("glow_squid", SoundObservationPolicy.mobSoundEmitter("entity.glow_squid.ambient"));
        assertEquals("skeleton_horse", SoundObservationPolicy.mobSoundEmitter("entity.skeleton_horse.ambient"));
        assertNull(SoundObservationPolicy.mobSoundEmitter("block.note_block.cat"));
        for (String suffix : List.of("puglin", "sad", "angry", "grumpy", "big", "cute")) {
            assertEquals("wolf", SoundObservationPolicy.mobSoundEmitter("entity.wolf_" + suffix + ".ambient"));
        }
        assertEquals("クロちゃん", SoundObservationPolicy.customName("  クロ\nちゃん  "));
        assertNull(SoundObservationPolicy.customName(" \r\n "));
        assertEquals(64, SoundObservationPolicy.customName("猫".repeat(100)).length());
    }
    @Test
    void strongerOminousSoundsSurviveLaterLowerSoundsAndSonicStaysImmediate() {
        var kinds = List.of("sculk_sensor", "sculk_shrieker", "warden_heartbeat", "warden_presence", "warden_sonic_boom");
        for (int current = 0; current < kinds.size(); current++) {
            for (int incoming = 0; incoming < kinds.size(); incoming++) {
                assertEquals(incoming >= current,
                    SoundObservationPolicy.replaceOminous(kinds.get(current), 20, kinds.get(incoming), 80),
                    kinds.get(current) + " -> " + kinds.get(incoming));
            }
        }
        assertTrue(SoundObservationPolicy.replaceOminous("", 0, "sculk_sensor", 80));
        assertFalse(SoundObservationPolicy.replaceOminous("sculk_sensor", 0, "unknown", 80));
        assertFalse(SoundObservationPolicy.replaceOminous("sculk_sensor", 0, null, 80));
    }

    @Test
    void aLowerSoundCanReplaceOnlyAfterTheRetainedSoundExpires() {
        assertFalse(SoundObservationPolicy.replaceOminous("sculk_shrieker", 80, "sculk_sensor", 80));
        assertTrue(SoundObservationPolicy.replaceOminous("sculk_shrieker", 81, "sculk_sensor", 80));
        assertTrue(SoundObservationPolicy.replaceOminous("sculk_sensor", 20, "sculk_shrieker", 80));
    }

}
