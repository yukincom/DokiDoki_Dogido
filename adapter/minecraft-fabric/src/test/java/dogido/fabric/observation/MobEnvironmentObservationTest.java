package dogido.fabric.observation;

import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 屋根で足元と身体上端の条件が分かれる境界と、負の座標の採取地点を検査する。 */
class MobEnvironmentObservationTest {
    @Test void rainAtBodyTopStillCountsWhenFeetAreCovered() {
        List<Integer> heights = new ArrayList<>();
        assertTrue(MobEnvironmentObservation.sampleRain(10.7, 64.1, 20.3, 65.9, (x, y, z) -> {
            assertEquals(10, x); assertEquals(20, z); heights.add(y); return y == 65;
        }));
        assertEquals(List.of(64,65), heights);
    }
    @Test void neitherFeetNorTopReceivesRainUnderCover() {
        assertFalse(MobEnvironmentObservation.sampleRain(0, 64, 0, 65.8, (x, y, z) -> false));
    }
    @Test void negativeCoordinatesUseContainingBlockRatherThanTruncation() {
        assertTrue(MobEnvironmentObservation.sampleRain(-0.2, -0.3, -1.1, 1.5, (x, y, z) -> {
            assertEquals(-1, x); assertEquals(-1, y); assertEquals(-2, z); return true;
        }));
    }
    @Test void bodyContactDoesNotReadTheNextBlockWhenBoxEndsOnAnInteger() {
        var box = new net.minecraft.util.math.Box(0,0,0,1.001,1.001,1.001);
        List<String> blocks = new ArrayList<>();
        assertFalse(ObservationOrigin.sampleWater(box, p -> {
            blocks.add(p.getX()+":"+p.getY()+":"+p.getZ());
            return p.getX() == 1 ? 1 : Double.NEGATIVE_INFINITY;
        }));
        assertEquals(List.of("0:0:0"),blocks);
    }
    @Test void fluidBelowTheBodyDoesNotCountButContactAtTheSurfaceDoes() {
        var box = new net.minecraft.util.math.Box(0,0.5,0,0.9,1.4,0.9);
        assertFalse(ObservationOrigin.sampleWater(box, p -> 0.4));
        assertTrue(ObservationOrigin.sampleWater(box, p -> 0.501));
    }
    @Test void waterAndRainRemainIndependentInTheWireObservation() {
        var submergedUnderRoof = new MobEnvironmentObservation(true, true, true, false, false, false).toJson();
        assertTrue(submergedUnderRoof.get("touching_water_or_rain").getAsBoolean());
        assertFalse(submergedUnderRoof.get("touching_rain").getAsBoolean());
        var both = new MobEnvironmentObservation(true, false, true, false, false, true).toJson();
        assertTrue(both.get("touching_rain").getAsBoolean());
        assertFalse(both.get("submerged_in_water").getAsBoolean());
    }
}
