package dogido.fabric;
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
}
