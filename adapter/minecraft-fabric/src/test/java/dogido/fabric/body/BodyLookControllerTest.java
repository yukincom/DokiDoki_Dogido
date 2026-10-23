package dogido.fabric.body;

import java.util.UUID;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static dogido.fabric.body.BodyInspectProtocol.*;
import static dogido.fabric.body.BodyLookController.*;

class BodyLookControllerTest {
    private final Context context = new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
    private final Point eye = new Point(10,65,20);
    @Test void eightDirectionsUseBodyYawAndConstantDistance() {
        double[][] offsets = {{0,1},{-1,1},{-1,0},{-1,-1},{0,-1},{1,-1},{1,0},{1,1}};
        for (int i=0;i<8;i++) {
            Point p = directionTarget(eye,0,Direction.values()[i]);
            double norm = Math.hypot(offsets[i][0],offsets[i][1]);
            assertEquals(eye.x()+8*offsets[i][0]/norm,p.x(),1e-9);
            assertEquals(eye.z()+8*offsets[i][1]/norm,p.z(),1e-9);
            assertEquals(eye.y(),p.y()); assertEquals(8,eye.distance(p),1e-9);
        }
        Point p = directionTarget(eye,90,Direction.FRONT);
        assertEquals(2,p.x(),1e-9); assertEquals(20,p.z(),1e-9);
        Point wrapped = directionTarget(eye,180,Direction.RIGHT);
        assertEquals(18,wrapped.x(),1e-9); assertEquals(20,wrapped.z(),1e-9);
        Point negative = directionTarget(eye,-180,Direction.RIGHT);
        assertEquals(wrapped.x(),negative.x(),1e-9); assertEquals(wrapped.z(),negative.z(),1e-9);
        assertThrows(IllegalArgumentException.class,() -> directionTarget(eye,Double.NaN,Direction.FRONT));
    }
    private final class Fake implements Access {
        Live live = new Live(context,100,20,200,eye,90,new Point(12,66.62,24));
        BodyConnection.LookRequest request; int calls;
        public Live capture() { return live; }
        public String submit(BodyConnection.LookRequest value) { calls++; request=value; return "queued"; }
    }
    @Test void playerSendsActualEyeAndDirectionSendsDerivedCoordinate() {
        Fake fake = new Fake(); var controller = new BodyLookController(fake);
        assertEquals("queued",controller.player(context,UUID.randomUUID()).status());
        assertEquals(fake.live.playerEye(),fake.request.target()); assertEquals(201,fake.request.sequence());
        assertEquals(context,fake.request.context()); assertEquals(100,fake.request.observedServerTick());
        assertEquals("queued",controller.direction(context,UUID.randomUUID(),Direction.RIGHT).status());
        assertEquals(directionTarget(eye,90,Direction.RIGHT),fake.request.target());
        assertEquals(202,fake.request.sequence());
    }
    @Test void replayKeepsOriginalPointAndTickAndRejectsDifferentIntent() {
        Fake fake = new Fake(); var controller = new BodyLookController(fake); UUID id = UUID.randomUUID();
        controller.direction(context,id,Direction.RIGHT); var original = fake.request;
        fake.live = new Live(context,110,0,201,eye,180,new Point(0,66,0));
        controller.direction(context,id,Direction.RIGHT); assertEquals(original,fake.request);
        assertEquals("action_id_conflict",controller.player(context,id).status()); assertEquals(2,fake.calls);
    }
    @Test void observedTargetKeepsTheOriginalPointWhenRetried() {
        Fake fake = new Fake(); var controller = new BodyLookController(fake); UUID id = UUID.randomUUID();
        Point target = new Point(14,68,22);
        assertEquals("queued",controller.point(context,id,target).status());
        var original = fake.request;
        assertEquals(target,original.target());
        fake.live = new Live(context,110,0,201,eye,180,new Point(0,66,0));
        assertEquals("queued",controller.point(context,id,new Point(12,66,24)).status());
        assertEquals(original,fake.request);
        assertEquals("action_id_conflict",controller.player(context,id).status());
    }
    @Test void pointBeyondTheExistingLookRangeNeverSends() {
        Fake fake = new Fake(); var controller = new BodyLookController(fake);
        assertEquals("invalid_target",controller.point(context,UUID.randomUUID(),new Point(100,65,20)).status());
        assertEquals(0,fake.calls);
    }
    @Test void staleMissingAndForeignContextNeverSend() {
        Fake fake = new Fake(); var controller = new BodyLookController(fake);
        var foreign = new Context(context.ownerId(),context.bodyId(),UUID.randomUUID(),context.dimension());
        assertEquals("stale_context",controller.player(foreign,UUID.randomUUID()).status());
        fake.live = new Live(context,100,751,0,eye,0,null);
        assertEquals("stale_snapshot",controller.player(context,UUID.randomUUID()).status());
        fake.live = new Live(context,100,0,0,eye,0,null);
        assertEquals("invalid_target",controller.player(context,UUID.randomUUID()).status());
        fake.live = null; assertEquals("stale_context",controller.player(context,UUID.randomUUID()).status());
        assertEquals(0,fake.calls);
    }
}
