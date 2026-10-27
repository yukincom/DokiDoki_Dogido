package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.*;
import java.util.UUID;
import net.minecraft.util.math.Vec3d;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class DirectedLookTest {
    @Test void applyingOrientationCompletesOnceWithoutAStabilityGate() {
        var look=new DirectedLook();UUID id=UUID.randomUUID();
        look.begin(id,new Position(0,8,8),10);
        assertEquals(id,look.applied(10));assertNull(look.applied(11));
        look.tick(109);assertTrue(look.active());look.tick(110);assertFalse(look.active());
    }
    @Test void everyLocomotionModeAndDisabledOrRetreatingControlAcceptsLook() {
        var context=new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
        for(Mode mode:Mode.values())for(boolean retreat:new boolean[]{false,true})for(boolean enabled:new boolean[]{false,true}) {
            var lease=new ControlLease();lease.reset(context);lease.local(mode,retreat);if(!enabled)lease.pause();
            var request=new ActionRequest(context,1,UUID.randomUUID(),10,20,Action.LOOK_AT,new Position(8,0,0),null);
            assertTrue(lease.submit(request,10,null).execute(),mode+" "+retreat+" "+enabled);
            lease.completeAction(request.actionId(),Outcome.SUCCEEDED,"facing_target");
            assertEquals(Outcome.SUCCEEDED,lease.lookResult().outcome());
        }
    }
    @Test void movementAndLookCompleteIndependentlyInEitherOrder() {
        for(Action movement:new Action[]{Action.MOVE_TO,Action.CALL,Action.WAIT,Action.REST})for(boolean lookFirst:new boolean[]{false,true}) {
            var context=new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
            var lease=new ControlLease();lease.reset(context);lease.local(Mode.DIRECT,false);
            var move=new ActionRequest(context,lookFirst?2:1,UUID.randomUUID(),10,20,movement,new Position(8,0,0),null);
            var look=new ActionRequest(context,lookFirst?1:2,UUID.randomUUID(),10,20,Action.LOOK_AT,new Position(0,8,0),null);
            assertTrue(lease.submit(lookFirst?look:move,10,null).execute());
            assertTrue(lease.submit(lookFirst?move:look,10,null).execute());
            assertTrue(lease.hasActiveAction());
            lease.completeAction(lookFirst?look.actionId():move.actionId(),Outcome.SUCCEEDED,lookFirst?"facing_target":"arrived");
            assertEquals(Outcome.ACCEPTED,lease.actionResult(lookFirst?move.actionId():look.actionId()).outcome());
            lease.completeAction(lookFirst?move.actionId():look.actionId(),Outcome.SUCCEEDED,lookFirst?"arrived":"facing_target");
            assertEquals(move.actionId(),lease.actionResult().actionId());assertEquals(Outcome.SUCCEEDED,lease.actionResult().outcome());
            assertEquals(look.actionId(),lease.lookResult().actionId());assertEquals(Outcome.SUCCEEDED,lease.lookResult().outcome());
        }
    }
    @Test void downedEndsBothPendingActions() {
        var context=new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
        var lease=new ControlLease();lease.reset(context);lease.local(Mode.DIRECT,false);
        var move=new ActionRequest(context,1,UUID.randomUUID(),10,20,Action.WAIT);
        var look=new ActionRequest(context,2,UUID.randomUUID(),10,20,Action.LOOK_AT,new Position(0,8,0),null);
        lease.submit(move,10,null);lease.submit(look,10,null);
        lease.interruptAll("downed");
        assertEquals(Outcome.INTERRUPTED,lease.actionResult().outcome());
        assertEquals(Outcome.INTERRUPTED,lease.submit(look,11,null).result().outcome());
        assertEquals("downed",lease.lookResult().reason());
    }
    @Test void controlExpiryEndsMovementWhileOrientationStillApplies() {
        var context=new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
        var lease=new ControlLease();lease.reset(context);
        lease.apply(new StateRequest(context,1,0,10,1,true,Mode.DIRECT,false),10);
        var move=new ActionRequest(context,1,UUID.randomUUID(),10,20,Action.WAIT);
        var look=new ActionRequest(context,2,UUID.randomUUID(),10,20,Action.LOOK_AT,new Position(0,8,0),null);
        lease.submit(move,10,null);lease.submit(look,10,null);lease.expire(11);
        assertEquals(Outcome.EXPIRED,lease.actionResult().outcome());assertEquals(Outcome.ACCEPTED,lease.lookResult().outcome());
        lease.completeAction(look.actionId(),Outcome.SUCCEEDED,"facing_target");
        assertEquals(Outcome.SUCCEEDED,lease.lookResult().outcome());assertFalse(lease.enabled());
    }
    @Test void consecutiveLookResultsStayReadableByIdWithoutBlockingNewLook() {
        var context=new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
        var lease=new ControlLease();lease.reset(context);
        UUID first=UUID.randomUUID(),second=UUID.randomUUID(),third=UUID.randomUUID();
        lease.submit(new ActionRequest(context,1,first,10,20,Action.LOOK_AT,new Position(0,8,0),null),10,null);
        assertTrue(lease.submit(new ActionRequest(context,2,second,10,20,Action.LOOK_AT,new Position(0,0,8),null),10,null).execute());
        lease.completeAction(second,Outcome.SUCCEEDED,"facing_target");
        lease.submit(new ActionRequest(context,3,third,10,20,Action.LOOK_AT,new Position(8,0,0),null),10,null);
        assertEquals(3,lease.lookResults().size());
        assertEquals(Outcome.INTERRUPTED,lease.actionResult(first).outcome());
        assertEquals(Outcome.SUCCEEDED,lease.actionResult(second).outcome());
        assertEquals(Outcome.ACCEPTED,lease.actionResult(third).outcome());
        assertTrue(lease.lookResults().contains(lease.actionResult(second)));
    }
    @Test void staleLookStillPublishesItsRejectionWithoutReservingAnAction() {
        var context=new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
        var lease=new ControlLease();lease.reset(context);
        var request=new ActionRequest(context,1,UUID.randomUUID(),10,20,Action.LOOK_AT,new Position(0,8,0),null);
        var rejected=lease.submit(request,31,null);
        assertEquals("stale_request",rejected.result().reason());assertFalse(rejected.execute());
        assertEquals(java.util.List.of(rejected.result()),lease.lookResults());assertNull(lease.actionResult(request.actionId()));
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
    @Test void newerCallReleasesLookAndEndsOnlyItsUnappliedResult() {
        for(boolean applied:new boolean[]{false,true}) {
            var context=new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
            var lease=new ControlLease();lease.reset(context);lease.local(Mode.FOLLOW,false);
            var look=new DirectedLook();UUID lookId=UUID.randomUUID(),callId=UUID.randomUUID();
            var request=new ActionRequest(context,1,lookId,10,20,Action.LOOK_AT,new Position(0,8,0),null);
            assertTrue(lease.submit(request,10,null).execute());look.begin(lookId,request.destination(),10);
            if(applied)lease.completeAction(look.applied(10),Outcome.SUCCEEDED,"facing_target");
            var call=new ActionRequest(context,2,callId,10,20,Action.CALL);
            assertTrue(lease.submit(call,10,null).execute());
            UUID previous=look.beginCall();
            if(previous!=null)lease.completeAction(previous,Outcome.INTERRUPTED,"superseded_by_call");
            assertFalse(look.active());assertNull(look.target());
            assertEquals(applied?Outcome.SUCCEEDED:Outcome.INTERRUPTED,lease.actionResult(lookId).outcome());
            assertFalse(lease.submit(call,11,null).execute());
            look.arrived(new Vec3d(0,2,0));assertTrue(look.arriving());
            look.arrivalTurnFinished();assertFalse(look.arriving());assertFalse(look.active());
        }
    }
    @Test void newerLookOwnsArrivalEvenAfterItsHoldExpires() {
        for(boolean expiresBeforeArrival:new boolean[]{false,true}) {
            var look=new DirectedLook();look.beginCall();
            look.begin(UUID.randomUUID(),new Position(8,0,0),10);look.applied(10);
            if(expiresBeforeArrival)look.tick(110);
            look.arrived(new Vec3d(0,2,0));
            assertFalse(look.arriving());assertEquals(!expiresBeforeArrival,look.active());
            look.tick(110);assertFalse(look.active());assertFalse(look.arriving());
        }
    }
    @Test void callCancellationPreservesLaterLookAndClearingCannotReviveArrival() {
        var look=new DirectedLook();look.beginCall();look.arrived(new Vec3d(0,2,0));
        look.cancelCall();assertFalse(look.arriving());look.arrived(Vec3d.ZERO);assertFalse(look.arriving());
        look.beginCall();look.begin(UUID.randomUUID(),new Position(8,0,0),10);
        look.cancelCall();assertTrue(look.active());
        look.clear();look.arrived(Vec3d.ZERO);assertFalse(look.active());assertFalse(look.arriving());
    }
}
