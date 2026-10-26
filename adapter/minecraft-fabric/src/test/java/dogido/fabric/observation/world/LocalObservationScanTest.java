package dogido.fabric.observation.world;

import java.util.HashSet;
import java.util.Set;
import net.minecraft.util.math.BlockPos;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static dogido.fabric.observation.world.ShelterObservation.DoorKind.*;

/** 同じ座標の二重取得と、走査範囲・未検出時の契約の変更を検出する。 */
class LocalObservationScanTest {
    private final BlockPos origin = new BlockPos(30, 70, -20);

    @Test void normalLightReadsEachPositionOnceAndIncludesBounds() {
        Set<BlockPos> read = new HashSet<>();
        Set<BlockPos> sources = Set.of(origin.add(4, 3, -4), origin.add(3, 0, 4), origin.add(5, 0, 0));
        var result = LightObservation.observeNearbyLightSources(origin, pos -> {
            assertTrue(read.add(pos), "duplicate read: " + pos); return sources.contains(pos);
        });
        assertEquals(567, read.size());
        assertEquals(2, result.count());
        assertEquals(5.0, result.nearestDistance());
        assertFalse(read.contains(origin.add(5, 0, 0)));
    }

    @Test void damagingLightKeepsItsDifferentVerticalAndHorizontalBounds() {
        Set<BlockPos> read = new HashSet<>();
        Set<BlockPos> sources = Set.of(origin.add(5, 2, 5), origin.add(0, -1, 0), origin.add(0, -2, 0));
        var result = LightObservation.observeNearbyDamagingLightSources(origin, pos -> {
            assertTrue(read.add(pos), "duplicate read: " + pos); return sources.contains(pos);
        });
        assertEquals(484, read.size());
        assertEquals(2, result.count());
        assertEquals(1.0, result.nearestDistance());
        assertFalse(read.contains(origin.add(0, -2, 0)));
    }

    @Test void missingAndOriginLightRetainDistanceContract() {
        var empty = LightObservation.observeNearbyLightSources(origin, pos -> false);
        assertEquals(0, empty.count()); assertEquals(99.0, empty.nearestDistance());
        var found = LightObservation.observeNearbyDamagingLightSources(origin, origin::equals);
        assertEquals(1, found.count()); assertEquals(0.0, found.nearestDistance());
    }

    @Test void doorsReturnTotalAndOpenCountFromTheSameHundredReads() {
        Set<BlockPos> read = new HashSet<>();
        var result = ShelterObservation.observeNearbyDoors(origin, pos -> {
            assertTrue(read.add(pos), "duplicate read: " + pos);
            if (pos.equals(origin.add(-2, -1, 2))) return OPEN;
            if (pos.equals(origin.add(2, 2, -2))) return CLOSED;
            return NONE;
        });
        assertEquals(100, read.size()); assertEquals(2, result.count()); assertEquals(1, result.openCount());
        assertFalse(read.contains(origin.add(0, 3, 0)));
        assertEquals(new ShelterObservation.Doors(0, 0), ShelterObservation.observeNearbyDoors(origin, pos -> NONE));
    }
}
