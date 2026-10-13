package dogido.fabric.body;

import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodyHearingPolicyTest {
    @Test void arrivalUsesBodyAtEmissionAndActualSoundRange() {
        var source = new Vec3d(0,64,0);
        assertTrue(BodyHearingPolicy.arrives(new Vec3d(12,64,0),source,12));
        assertFalse(BodyHearingPolicy.arrives(new Vec3d(40,64,0),source,12));
        assertFalse(BodyHearingPolicy.arrives(source,source,Double.NaN));
    }
    @Test void snapshotAndTransportAgeAreAddedExactlyOnceAndNeverRenewTheEmission() {
        assertEquals(550,BodyHearingPolicy.serverSoundAge(100,90,50));
        assertEquals(500,BodyHearingPolicy.referenceAge(550,50));
        assertEquals(800,BodyHearingPolicy.serverSoundAge(100,90,300));
        assertEquals(500,BodyHearingPolicy.referenceAge(800,300));
        assertEquals(-1,BodyHearingPolicy.serverSoundAge(100,101,0));
        assertEquals(-1,BodyHearingPolicy.serverSoundAge(400,90,0));
    }
}
