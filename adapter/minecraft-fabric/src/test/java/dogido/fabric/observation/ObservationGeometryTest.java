package dogido.fabric.observation;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class ObservationGeometryTest {
    @Test void bodyDistanceHeightAndDirectionAreIndependentOfOtherActors() {
        var body = origin(10,70,20,0);
        var owner = origin(50,64,20,180);
        var target = new net.minecraft.util.math.Vec3d(10,70,24);
        assertEquals("front",ObservationGeometry.classifyHorizontal(body,target));
        assertEquals("same",ObservationGeometry.classifyVertical(body,target));
        assertEquals("above",ObservationGeometry.classifyVertical(owner,target));
        assertEquals(4,body.distanceTo(target));
        assertTrue(owner.distanceTo(target) > 40);
        owner = origin(50,64,20,90);
        assertEquals("front",ObservationGeometry.classifyHorizontal(body,target));
        assertEquals("back",ObservationGeometry.classifyHorizontal(origin(10,70,20,180),target));
    }
    private static ObservationOrigin origin(double x,double y,double z,float yaw) {
        var position = new net.minecraft.util.math.Vec3d(x,y,z);
        return new ObservationOrigin(java.util.UUID.randomUUID(),java.util.UUID.randomUUID(),position,position.add(0,1,0),yaw,0,
            new net.minecraft.util.math.Box(x-.3,y,z-.3,x+.3,y+1,z+.3),null,null);
    }
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
