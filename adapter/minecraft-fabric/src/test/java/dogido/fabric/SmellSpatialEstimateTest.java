package dogido.fabric;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import java.util.List;
import org.junit.jupiter.api.Test;

final class SmellSpatialEstimateTest {
    private static SmellSpatialEstimate.Point point(double x, double y, double z) {
        return new SmellSpatialEstimate.Point(x, y, z);
    }
    private static SmellPolicy.Observation smell(SmellSpatialEstimate.Point observer,
                                               SmellSpatialEstimate.Point source, String id, int temperature) {
        return SmellPolicy.resolve(List.of(SmellPolicy.zombieCandidate("zombie", id,
            Math.sqrt(observer.squaredDistance(source)))), temperature, false, null);
    }
    private static SmellSpatialEstimate.Estimate estimate(SmellSpatialEstimate.Point source) {
        var observer = point(0, 0, 0);
        return SmellSpatialEstimate.resolve(smell(observer, source, "z1", 0), observer, source);
    }
    @Test
    void allEightBearingsUseCurrentSourceWithoutRequiringPlayerMovement() {
        String[] directions = {"north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"};
        double[][] coordinates = {{0,-4},{4,-4},{4,0},{4,4},{0,4},{-4,4},{-4,0},{-4,-4}};
        for (int i = 0; i < directions.length; i++) {
            var result = estimate(point(coordinates[i][0], 0, coordinates[i][1]));
            assertEquals(directions[i], result.cardinal());
            assertNull(result.vertical());
        }
    }
    @Test
    void approachingAndSwitchingWinnersUseTheirCurrentBearing() {
        var observer = point(0, 0, 0);
        for (double x : new double[] {7, 5, 3, 1.5}) {
            var source = point(x, 0, 0);
            var result = SmellSpatialEstimate.resolve(smell(observer, source, "z1", 0), observer, source);
            assertEquals("east", result.cardinal());

        }
        var next = point(-3, 0, 0);
        var result = SmellSpatialEstimate.resolve(smell(observer, next, "z2", 0), observer, next);
        assertEquals("west", result.cardinal());
    }
    @Test
    void aboveBelowAndCloseSourcesKeepOnlyKnownAxes() {
        assertEquals(new SmellSpatialEstimate.Estimate(null, "above"), estimate(point(0, 3, 0)));
        assertEquals(new SmellSpatialEstimate.Estimate(null, "below"), estimate(point(0, -3, 0)));
        assertEquals("southeast", estimate(point(0.5, 0, 0.5)).cardinal());
        assertNull(estimate(point(0, 0, 0)));
    }
    @Test
    void missingPositionDoesNotInventDirection() {
        var observer = point(0,0,0);
        var observation = smell(observer, point(3,0,0), "z1", 0);
        var result = SmellSpatialEstimate.resolve(observation, observer, null);
        assertNull(result);
        assertNull(SmellSpatialEstimate.resolve(observation, observer, point(Double.NaN,0,0)));
    }
    @Test
    void mixedHeldBiomeRainAfterAndSuppressedSmellsHaveNoExternalSpatialEstimate() {
        var bread = SmellPolicy.hotbarCandidate("bread");
        List<SmellPolicy.Observation> observations = List.of(
            SmellPolicy.resolve(List.of(), 0, false, null),
            SmellPolicy.resolve(List.of(bread), 0, false, "rain"),
            SmellPolicy.resolve(List.of(bread), 0, false, null),
            SmellPolicy.resolve(List.of(SmellPolicy.biomeCandidate("swamp")), 0, false, null),
            SmellPolicy.resolve(List.of(bread, SmellPolicy.biomeCandidate("swamp")), 0, false, null),
            SmellPolicy.resolve(List.of(bread, SmellPolicy.hotbarCandidate("cookie")), 0, false, null),
            SmellPolicy.resolve(List.of(SmellPolicy.rainAfterCandidate("earth", 1)), 0, true, null)
        );
        for (var observation : observations) {
            assertNull(SmellSpatialEstimate.resolve(observation, point(0,0,0), point(3,0,0)));
        }
    }
    @Test
    void cachedBlockSourceRemeasuresDistanceWhenPlayerMovesBetweenScans() {
        var cached = new SmellSpatialEstimate.LocatedCandidate(
            SmellPolicy.blockCandidate("lilac", "block:lilac@position", 5, false, false), point(5,0,0));
        var before = SmellPolicy.resolve(List.of(cached.observedFrom(point(0,0,0))), 0, false, null);
        var after = SmellPolicy.resolve(List.of(cached.observedFrom(point(2,0,0))), 0, false, null);
        assertEquals(1, before.effectiveStrength());
        assertEquals(3, after.effectiveStrength());
    }
}
