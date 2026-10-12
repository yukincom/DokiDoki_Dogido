package dogido.lab;

import dogido.lab.api.BodyProtocol.Visibility;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 距離・視野・同一点の遮蔽と未取得を検査する。期待配置を固定し、説明文の正誤とは分ける。 */
class BodyVisionTest {
    private void visible(double yaw, double pitch, Vec3d target, boolean expected) {
        var observation = BodyVision.observe(50,Vec3d.ZERO,yaw,pitch,List.of(target),p -> BodyVisibility.Trace.CLEAR);
        assertEquals(expected ? Visibility.VISIBLE : Visibility.NOT_VISIBLE,observation.playerVisibility());
        assertEquals(50,observation.serverTick());
    }
    @Test void cardinalDirectionsFollowMinecraftYawAndAngleWrap() {
        visible(0,0,new Vec3d(0,0,4),true);
        visible(90,0,new Vec3d(-4,0,0),true);
        visible(-90,0,new Vec3d(4,0,0),true);
        visible(180,0,new Vec3d(0,0,-4),true);
        visible(270,0,new Vec3d(4,0,0),true);
        visible(-540,0,new Vec3d(0,0,-4),true);
        visible(0,0,new Vec3d(0,0,-4),false);
        visible(0,0,new Vec3d(4,0,0),false);
        visible(179,0,new Vec3d(.01,0,-4),true);
    }
    @Test void horizontalVerticalAndRangeBoundariesAreInclusive() {
        visible(0,0,new Vec3d(Math.sqrt(3)*2,0,2),true);
        visible(0,0,new Vec3d(Math.sqrt(3)*2+.001,0,2),false);
        visible(0,0,new Vec3d(0,4,4),true);
        visible(0,0,new Vec3d(0,4.001,4),false);
        visible(0,0,new Vec3d(0,0,16),true);
        visible(0,0,new Vec3d(0,0,16.001),false);
        visible(0,45,new Vec3d(0,-4,4),true);
        visible(0,45,new Vec3d(0,4,4),false);
    }
    @Test void rayUsesTheBodyEyeAndOnlyPointsInsideItsFov() {
        Vec3d eye = new Vec3d(100,80,-200), front = eye.add(0,0,4), behind = eye.add(0,0,-4);
        AtomicInteger calls = new AtomicInteger();
        var result = BodyVision.observe(1,eye,0,0,List.of(front,behind),p -> {
            calls.incrementAndGet(); assertEquals(front,p); return BodyVisibility.Trace.BLOCKED;
        });
        assertEquals("occluded",result.reason());
        assertEquals(1,calls.get(),"a clear point behind the body must not repair a blocked point in front");
    }
    @Test void partialOcclusionAndUnknownSpaceHaveDifferentResults() {
        Vec3d low = new Vec3d(0,0,5), high = new Vec3d(0,1,5);
        var partial = BodyVision.observe(1,Vec3d.ZERO,0,0,List.of(low,high),p ->
            p.equals(high) ? BodyVisibility.Trace.CLEAR : BodyVisibility.Trace.BLOCKED);
        assertEquals(Visibility.VISIBLE,partial.playerVisibility());
        var unknown = BodyVision.observe(1,Vec3d.ZERO,0,0,List.of(low,high),p ->
            p.equals(high) ? BodyVisibility.Trace.UNAVAILABLE : BodyVisibility.Trace.BLOCKED);
        assertEquals(Visibility.UNAVAILABLE,unknown.playerVisibility());
        assertEquals("area_unloaded",unknown.reason());
        var clear = BodyVision.observe(1,Vec3d.ZERO,0,0,List.of(low,high),p ->
            p.equals(high) ? BodyVisibility.Trace.CLEAR : BodyVisibility.Trace.UNAVAILABLE);
        assertEquals(Visibility.VISIBLE,clear.playerVisibility());
    }
    @Test void outOfRangeAndOutsideFovDoNotReadWorldAndInvalidPoseIsUnknown() {
        var far = BodyVision.observe(1,Vec3d.ZERO,0,0,List.of(new Vec3d(0,0,17)),p -> fail("outside range"));
        assertEquals("out_of_range",far.reason());
        var back = BodyVision.observe(1,Vec3d.ZERO,0,0,List.of(new Vec3d(0,0,-4)),p -> fail("outside FOV"));
        assertEquals("outside_fov",back.reason());
        var bad = BodyVision.observe(1,Vec3d.ZERO,Double.NaN,0,List.of(new Vec3d(0,0,4)),p -> fail("invalid pose"));
        assertEquals("invalid_pose",bad.reason());
        assertEquals(Visibility.UNAVAILABLE,bad.playerVisibility());
    }
}
