package dogido.fabric.observation.world;

import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class OverheadObservationTest {
    @Test void ceilingDistanceAndMaterialComeFromOneScan() {
        AtomicInteger reads = new AtomicInteger();
        OverheadObservation result = OverheadObservation.scan(step -> {
            assertEquals(reads.incrementAndGet(), step);
            return step == 3 ? "foliage" : "air";
        });
        assertEquals(new OverheadObservation(3, "foliage", true), result);
        assertEquals(3, reads.get());
    }
    @Test void ceilingAtLimitIsDifferentFromNoCeilingWithinTheLimit() {
        AtomicInteger reads = new AtomicInteger();
        OverheadObservation open = OverheadObservation.scan(step -> {
            reads.incrementAndGet(); return "air";
        });
        assertEquals(new OverheadObservation(24, "none", false), open);
        assertEquals(24, reads.get());
        assertEquals(new OverheadObservation(24, "stone", true),
            OverheadObservation.scan(step -> step == 24 ? "stone" : "air"));
    }
    @Test void waterDoesNotBecomeASolidCeilingButTheCoverBeyondItIsDetected() {
        assertEquals(new OverheadObservation(24, "fluid", false),
            OverheadObservation.scan(step -> "fluid"));
        assertEquals(new OverheadObservation(4, "wood", true),
            OverheadObservation.scan(step -> step == 4 ? "wood" : "fluid"));
        assertEquals(new OverheadObservation(1, "solid", true),
            OverheadObservation.scan(step -> "solid"));
    }
}
