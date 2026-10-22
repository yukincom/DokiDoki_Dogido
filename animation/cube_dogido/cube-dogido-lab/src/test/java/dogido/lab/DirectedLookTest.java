package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.*;
import java.util.UUID;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class DirectedLookTest {
    @Test void completesOnlyAfterRotationAndHoldsUntilDeadline() {
        var look = new DirectedLook(); UUID id = UUID.randomUUID();
        look.begin(id,new Position(-8,0,0),10);
        assertNull(look.reached(Vec3d.ZERO,0,0));
        assertEquals(id,look.reached(Vec3d.ZERO,90,0));
        assertNull(look.reached(Vec3d.ZERO,90,0));
        assertNull(look.stop(109,null)); assertTrue(look.active());
        assertNull(look.stop(110,null)); assertFalse(look.active());
    }
    @Test void checksVerticalPitchAndTimeoutBeforeSuccess() {
        var look = new DirectedLook(); look.begin(UUID.randomUUID(),new Position(0,8,8),0);
        assertNull(look.reached(Vec3d.ZERO,0,0));
        assertNotNull(look.reached(Vec3d.ZERO,0,-45));
        look.begin(UUID.randomUUID(),new Position(0,0,-8),10);
        var result = look.stop(110,null); assertEquals(Outcome.EXPIRED,result.outcome());
        assertFalse(look.active()); assertNull(look.reached(Vec3d.ZERO,180,0));
    }
    @Test void yieldsToPrioritiesAndCancelsWithoutSuccess() {
        assertNull(DirectedLook.priority(true,false,false,false,Activity.HOLDING,false,false));
        assertEquals("body_action_busy",DirectedLook.priority(true,false,false,true,Activity.HOLDING,false,false));
        assertEquals("distance_follow_has_priority",DirectedLook.priority(true,false,false,false,Activity.HOLDING,true,false));
        assertEquals("danger_has_priority",DirectedLook.priority(true,false,false,false,Activity.HOLDING,false,true));
        assertEquals("call_has_priority",DirectedLook.priority(true,false,true,false,Activity.CALLING,false,false));
        var look = new DirectedLook(); look.begin(UUID.randomUUID(),new Position(0,0,8),0);
        assertEquals(Outcome.INTERRUPTED,look.stop(2,"danger_has_priority").outcome()); assertFalse(look.active());
    }
    @Test void validatesCoordinateAndUsesExistingDedupAndEpochChecks() {
        var context = new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
        var request = new ActionRequest(context,1,UUID.randomUUID(),10,20,Action.LOOK_AT,new Position(8,0,0),null);
        for (Mode mode : Mode.values()) assertNull(BodyRequests.action(request,mode,false,new Position(0,0,0)));
        assertEquals("invalid_destination",BodyRequests.action(new ActionRequest(context,1,UUID.randomUUID(),10,20,Action.LOOK_AT),Mode.FOLLOW,false,new Position(0,0,0)));
        var lease = new ControlLease(); lease.reset(context); lease.local(Mode.FOLLOW,false);
        assertTrue(lease.submit(request,10,null).execute());
        assertFalse(lease.submit(request,11,null).execute());
        lease.completeAction(request.actionId(),Outcome.SUCCEEDED,"facing_target");
        assertEquals(Outcome.SUCCEEDED,lease.submit(request,12,null).result().outcome());
        lease.reset(new Context(context.ownerId(),context.bodyId(),UUID.randomUUID(),context.dimension()));
        assertEquals("stale_context",lease.submit(request,12,null).result().reason());
    }
}
