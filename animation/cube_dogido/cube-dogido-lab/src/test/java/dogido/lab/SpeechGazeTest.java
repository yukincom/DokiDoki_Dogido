package dogido.lab;

import com.google.gson.Gson;
import dogido.lab.api.BodyProtocol.*;
import dogido.lab.api.perception.BodyVisionGeometry;
import dogido.lab.api.perception.BodyVisibility;
import dogido.lab.control.SpeechGaze;
import java.util.List;
import java.util.UUID;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class SpeechGazeTest {
    private final Context context=new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
    private final Vec3d eye=new Vec3d(100,65,-30);
    private SpeechGazeObservation observation(long sequence,long tick,Position point) {
        return new SpeechGazeObservation(context,sequence,tick,point);
    }
    @Test void expiresFromObservationAndSameTickEndCannotBeUndoneByOldDelivery() {
        var gaze=new SpeechGaze();
        var start=observation(1,100,new Position(102,67,-28));
        assertTrue(gaze.observe(start,context,100,eye));
        assertEquals(new Vec3d(102,67,-28),gaze.target(119));
        assertNull(gaze.target(120));
        assertFalse(gaze.observe(start,context,119,eye));
        assertTrue(gaze.observe(observation(2,100,null),context,119,eye));
        assertNull(gaze.target(119));
        assertFalse(gaze.observe(start,context,119,eye));
    }
    @Test void rejectsOtherWorldFutureStaleAndUnboundedPoints() {
        var gaze=new SpeechGaze();
        var other=new Context(context.ownerId(),context.bodyId(),UUID.randomUUID(),context.dimension());
        assertFalse(gaze.observe(observation(1,100,new Position(100,65,-30)),other,100,eye));
        assertFalse(gaze.observe(observation(1,101,null),context,100,eye));
        assertFalse(gaze.observe(observation(1,80,null),context,100,eye));
        assertFalse(gaze.observe(observation(1,100,new Position(Double.NaN,65,-30)),context,100,eye));
        assertFalse(gaze.observe(observation(1,100,new Position(165,65,-30)),context,100,eye));
        assertNull(gaze.target(100));
    }
    @Test void ownerVisibilityAndGazeShareWorldCoordinateAnglesIncludingUpDown() {
        for (Vec3d delta : List.of(new Vec3d(0,0,4),new Vec3d(-4,0,0),new Vec3d(0,0,-4),
                new Vec3d(4,0,0),new Vec3d(3,3,3),new Vec3d(-3,-3,-3),new Vec3d(0,4,0),new Vec3d(0,-4,0))) {
            var angle=BodyVisionGeometry.facing(delta,37,0);
            for (Vec3d origin : List.of(Vec3d.ZERO,eye)) {
                var result=BodyVisionGeometry.observe(1,origin,angle.yaw(),angle.pitch(),List.of(origin.add(delta)),point -> BodyVisibility.Trace.CLEAR);
                assertEquals(Visibility.VISIBLE,result.playerVisibility());
            }
        }
        assertEquals(90,BodyVisionGeometry.facing(new Vec3d(-1,0,0),0,0).yaw());
        assertEquals(-90,BodyVisionGeometry.facing(new Vec3d(0,1,0),37,0).pitch());
        assertEquals(37,BodyVisionGeometry.facing(new Vec3d(0,1,0),37,0).yaw());
        assertEquals(new BodyVisionGeometry.Angles(37,12),BodyVisionGeometry.facing(Vec3d.ZERO,37,12));
    }
    @Test void fabricPayloadPreservesWorldPointAndExplicitRelease() {
        var gson=new Gson();
        for (Position target : new Position[]{new Position(100,70,-25),null}) {
            var packet=new BodyPackets.Message(8,"speech_gaze",null,null,null,observation(4,100,target));
            assertEquals(packet,gson.fromJson(gson.toJson(packet),BodyPackets.Message.class));
        }
    }
}
