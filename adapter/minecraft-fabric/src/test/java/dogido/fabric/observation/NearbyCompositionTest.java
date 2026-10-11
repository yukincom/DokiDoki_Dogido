package dogido.fabric.observation;

import java.util.List;
import java.util.stream.IntStream;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class NearbyCompositionTest {
    @Test void orderAndDuplicatesDoNotChangeTheKinds() {
        assertEquals(NearbyComposition.types(List.of("cow","pig","cow")), NearbyComposition.types(List.of("pig","cow")));
        assertFalse(NearbyComposition.types(List.of()).get("truncated").getAsBoolean());
    }
    @Test void overflowIsExplicitSoMissingKindsAreNotAbsence() {
        var result=NearbyComposition.types(IntStream.range(0,129).mapToObj(i -> "test:kind_"+i).toList());
        assertTrue(result.get("truncated").getAsBoolean());
        assertEquals(128,result.getAsJsonArray("ids").size());
    }
    @Test void boundaryDoesNotRediscoverTheSameSpeciesAndNeverRetainsAnUnseenOne() {
        var tracker = new NearbyComposition.RangeTracker(NearbyComposition.MOB_ENTER, NearbyComposition.MOB_EXIT);
        assertFalse(tracker.allows("cow", 16.1));
        assertTrue(tracker.allows("cow", 16));
        tracker.finish(List.of("cow", "cow"));
        // 同種の別個体でも、16〜21の間なら種類表は変わらない。
        for (double distance : List.of(16.1, 20.9, 21.0)) assertTrue(tracker.allows("cow", distance));
        assertFalse(tracker.allows("pig", 20));
        assertFalse(tracker.allows("cow", 21.1));
        tracker.finish(List.of()); // 外周を超える・見通しを失うと、現在の表から外れる。
        assertFalse(tracker.allows("cow", 20));
        assertTrue(tracker.allows("cow", 11));
        tracker.finish(List.of("cow"));
        tracker.reset();
        assertFalse(tracker.allows("cow", 20));
        assertFalse(tracker.allows("cow", Double.NaN));
    }
    @Test void blockBoundaryKeepsExistingAcquisitionBoxAndAddsFiveBlocksForRetention() {
        var tracker = new NearbyComposition.RangeTracker(0, NearbyComposition.BLOCK_MARGIN);
        assertTrue(tracker.allows("stone", NearbyComposition.blockPadding(8, -3, 8)));
        assertTrue(tracker.allows("stone", NearbyComposition.blockPadding(8, 4, 8)));
        assertFalse(tracker.allows("stone", NearbyComposition.blockPadding(9, 0, 0)));
        assertFalse(tracker.allows("stone", NearbyComposition.blockPadding(0, -4, 0)));
        tracker.finish(List.of("stone"));
        assertTrue(tracker.allows("stone", NearbyComposition.blockPadding(13, -8, 13)));
        assertTrue(tracker.allows("stone", NearbyComposition.blockPadding(13, 9, 13)));
        assertFalse(tracker.allows("stone", NearbyComposition.blockPadding(14, 0, 0)));
        assertFalse(tracker.allows("stone", NearbyComposition.blockPadding(0, 10, 0)));
        tracker.finish(List.of());
        assertFalse(tracker.allows("stone", NearbyComposition.blockPadding(13, 0, 13)));
    }

    @Test void quantityIsExactBelowFiveAndManyFromFiveWithoutCappingTheScanAtFour() {
        for (int count : List.of(1, 4, 5, 25)) {
            var mobs = IntStream.range(0, count).mapToObj(i -> new NearbyComposition.Mob("cow", false, null)).toList();
            var row = NearbyComposition.mobs(mobs).getAsJsonArray("entries").get(0).getAsJsonObject();
            assertEquals(count < 5 ? Integer.toString(count) : "many", row.get("quantity").getAsString());
            assertEquals(0, row.getAsJsonArray("professions").size());
        }
    }
    @Test void villagerTotalAndExclusiveProfessionSubsetsUseTheSameThreshold() {
        var mobs = new java.util.ArrayList<NearbyComposition.Mob>();
        for (int i=0; i<6; i++) mobs.add(new NearbyComposition.Mob("villager", false, "librarian"));
        for (int i=0; i<3; i++) mobs.add(new NearbyComposition.Mob("villager", false, "farmer"));
        mobs.add(new NearbyComposition.Mob("villager", true, "none"));
        mobs.add(new NearbyComposition.Mob("villager", false, "none"));
        mobs.add(new NearbyComposition.Mob("villager", false, "nitwit"));
        mobs.add(new NearbyComposition.Mob("villager", false, null));
        var row = NearbyComposition.mobs(mobs).getAsJsonArray("entries").get(0).getAsJsonObject();
        assertEquals("many", row.get("quantity").getAsString());
        var groups = new java.util.TreeMap<String, String>();
        row.getAsJsonArray("professions").forEach(value -> {
            var group = value.getAsJsonObject();
            groups.put(group.get("id").getAsString(), group.get("quantity").getAsString());
        });
        assertEquals(java.util.Map.of("librarian","many","farmer","3","baby","1","none","1","nitwit","1","unknown","1"), groups);
        java.util.Collections.reverse(mobs);
        assertEquals(row, NearbyComposition.mobs(mobs).getAsJsonArray("entries").get(0));
    }
    @Test void mobAndProfessionOverflowAreExplicit() {
        var mobs = IntStream.range(0,129).mapToObj(i -> new NearbyComposition.Mob("kind_"+i, false, null)).toList();
        assertTrue(NearbyComposition.mobs(mobs).get("truncated").getAsBoolean());
        assertEquals(128, NearbyComposition.mobs(mobs).getAsJsonArray("entries").size());
        var villagers = IntStream.range(0,129).mapToObj(i -> new NearbyComposition.Mob("villager", false, "job_"+i)).toList();
        assertTrue(NearbyComposition.mobs(villagers).get("truncated").getAsBoolean());
        assertEquals(128, NearbyComposition.mobs(villagers).getAsJsonArray("entries").get(0).getAsJsonObject().getAsJsonArray("professions").size());
    }
}
