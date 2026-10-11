package dogido.fabric.observation;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class ObservationGeometryTest {
    @Test void distanceBandsKeepTheirInclusiveUpperBoundary() {
        assertEquals("touching", ObservationGeometry.bucketDistance(1.5));
        assertEquals("very_close", ObservationGeometry.bucketDistance(Math.nextUp(1.5)));
        assertEquals("very_close", ObservationGeometry.bucketDistance(4.0));
        assertEquals("close", ObservationGeometry.bucketDistance(Math.nextUp(4.0)));
        assertEquals("close", ObservationGeometry.bucketDistance(8.0));
        assertEquals("mid", ObservationGeometry.bucketDistance(Math.nextUp(8.0)));
        assertEquals("mid", ObservationGeometry.bucketDistance(16.0));
        assertEquals("far", ObservationGeometry.bucketDistance(Math.nextUp(16.0)));
        assertTrue(ObservationGeometry.distanceBandRank("touching") < ObservationGeometry.distanceBandRank("far"));
    }
}
