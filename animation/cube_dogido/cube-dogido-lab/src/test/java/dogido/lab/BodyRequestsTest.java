package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.*;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodyRequestsTest {
    private final Context context = new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
    private final Position origin = new Position(0,64,0);
    private ActionRequest request(Action action, Position destination, String food) {
        return new ActionRequest(context,1,UUID.randomUUID(),10,40,action,destination,food);
    }
    @Test void externalStateRequiresDirectControlInsteadOfCodeChosenExplorationOrRetreat() {
        for (Mode legacy : new Mode[]{Mode.EXPLORE,Mode.FOLLOW,Mode.HOLD})
            assertEquals("model_control_requires_direct_mode",BodyRequests.state(new StateRequest(context,1,0,10,40,true,legacy,false),false));
        assertEquals("direct_mode_requires_chosen_destination",BodyRequests.state(new StateRequest(context,1,0,10,40,true,Mode.DIRECT,true),false));
        assertNull(BodyRequests.state(new StateRequest(context,1,0,10,40,true,Mode.DIRECT,false),false));
    }
    @Test void malformedAndOverlongDestinationsCannotReachTheGameExecutor() {
        assertEquals("invalid_destination",BodyRequests.action(request(Action.MOVE_TO,null,null),Mode.DIRECT,false,origin));
        assertEquals("invalid_destination",BodyRequests.action(request(Action.MOVE_TO,new Position(Double.NaN,64,0),null),Mode.DIRECT,false,origin));
        assertEquals("destination_too_far",BodyRequests.action(request(Action.MOVE_TO,new Position(33,64,0),null),Mode.DIRECT,false,origin));
        assertEquals("unexpected_destination",BodyRequests.action(request(Action.REST,origin,null),Mode.DIRECT,false,origin));
        assertNull(BodyRequests.action(request(Action.MOVE_TO,new Position(6,64,-2),null),Mode.DIRECT,false,origin));
    }
    @Test void eatNeedsOneObservedTargetAndDownedBodiesRejectAllActionKinds() {
        assertEquals("invalid_food_id",BodyRequests.action(request(Action.EAT,null,null),Mode.DIRECT,false,origin));
        assertEquals("unexpected_food_id",BodyRequests.action(request(Action.WAIT,null,"item:x"),Mode.DIRECT,false,origin));
        for (Action action : Action.values()) assertEquals("body_downed",BodyRequests.action(request(action,null,null),Mode.DIRECT,true,origin));
    }
    @Test void replayingConsumedFoodReturnsTheSavedResultInsteadOfExecutingConsumptionAgain() {
        ControlLease control = new ControlLease(); control.reset(context);
        control.apply(new StateRequest(context,1,0,10,100,true,Mode.DIRECT,false),10);
        ActionRequest eat = request(Action.EAT,null,"item:"+UUID.randomUUID());
        int consumed = 0;
        if (control.request(eat,10,null).outcome() == Outcome.ACCEPTED) {
            consumed++; control.completeAction(Outcome.SUCCEEDED,"food_consumed");
        }
        // The item has gone. The same ID must still return its old terminal result, before admission.
        if (control.request(eat,11,"food_changed").outcome() == Outcome.ACCEPTED) consumed++;
        assertEquals(1,consumed); assertEquals(Outcome.SUCCEEDED,control.actionResult().outcome());
        control.reset(new Context(context.ownerId(),context.bodyId(),UUID.randomUUID(),context.dimension()));
        assertEquals("stale_context",control.request(eat,12,null).reason());
    }
    @Test void cachedAcceptanceCannotDispatchAgainOrBeReusedForAnotherKindOfAction() {
        ControlLease control = new ControlLease(); control.reset(context);
        control.apply(new StateRequest(context,1,0,10,100,true,Mode.DIRECT,false),10);
        ActionRequest move = request(Action.MOVE_TO,new Position(4,64,0),null);
        assertTrue(control.submit(move,10,null).execute());
        var retry = control.submit(move,11,null);
        assertEquals(Outcome.ACCEPTED,retry.result().outcome()); assertFalse(retry.execute());
        var substitution = control.submit(new ActionRequest(context,move.sequence(),move.actionId(),10,40,Action.EAT,null,"item:x"),11,null);
        assertFalse(substitution.execute()); assertEquals("action_id_conflict",substitution.result().reason());
        control.completeAction(Outcome.SUCCEEDED,"arrived");
        assertEquals("arrived",control.submit(move,12,null).result().reason());
    }
    @Test void followOnlyAddsMoveToAndKeepsExternalEnableAndOtherModesRestricted() {
        var move = request(Action.MOVE_TO,new Position(6,64,0),null);
        assertNull(BodyRequests.action(move,Mode.FOLLOW,false,origin));
        for (Mode mode : new Mode[]{Mode.HOLD,Mode.EXPLORE})
            assertEquals("direct_mode_required",BodyRequests.action(move,mode,false,origin));
        for (Action action : new Action[]{Action.REST,Action.WAIT,Action.EAT})
            assertEquals("direct_mode_required",BodyRequests.action(request(action,null,null),Mode.FOLLOW,false,origin));
    }
    @Test void inspectionCannotOverridePriorityOrLeaveTheOwnersFollowRange() {
        assertNull(BodyRequests.inspection(false,false,Activity.HOLDING,16,16,16));
        assertEquals("call_has_priority",BodyRequests.inspection(true,false,Activity.HOLDING,2,3,16));
        assertEquals("body_action_busy",BodyRequests.inspection(false,true,Activity.HOLDING,2,3,16));
        for (Activity activity : new Activity[]{Activity.FOLLOWING,Activity.RETREATING,Activity.MOVING,Activity.DOWNED})
            assertEquals("body_activity_busy",BodyRequests.inspection(false,false,activity,2,3,16));
        assertEquals("distance_follow_has_priority",BodyRequests.inspection(false,false,Activity.HOLDING,16.01,3,16));
        assertEquals("inspection_outside_follow_distance",BodyRequests.inspection(false,false,Activity.HOLDING,2,16.01,16));
    }
    @Test void inspectionResultOnlyCompletesTheMatchingAcceptedAction() {
        ControlLease control = new ControlLease(); control.reset(context); control.local(Mode.FOLLOW,false);
        ActionRequest move = request(Action.MOVE_TO,new Position(4,64,0),null);
        assertTrue(control.submit(move,10,null).execute());
        control.completeAction(UUID.randomUUID(),Outcome.SUCCEEDED,"arrived");
        assertTrue(control.hasActiveAction()); assertEquals(Outcome.ACCEPTED,control.actionResult().outcome());
        control.completeAction(move.actionId(),Outcome.SUCCEEDED,"arrived");
        assertFalse(control.hasActiveAction()); assertEquals(Outcome.SUCCEEDED,control.actionResult().outcome());
        assertFalse(control.submit(move,11,"body_activity_busy").execute());
        assertEquals("arrived",control.actionResult().reason());
    }

    @Test void ownerApproachHasNoExternalDestinationAndCannotBorrowOtherActionModes() {
        for (MovePurpose purpose : new MovePurpose[]{MovePurpose.OWNER_NEAR,MovePurpose.OWNER_FACE}) {
            var request = new ActionRequest(context,1,UUID.randomUUID(),10,40,Action.MOVE_TO,null,null,purpose);
            assertNull(BodyRequests.action(request,Mode.FOLLOW,false,origin));
            assertEquals("owner_approach_requires_follow",BodyRequests.action(request,Mode.DIRECT,false,origin));
            assertEquals("unexpected_destination",BodyRequests.action(new ActionRequest(context,1,request.actionId(),10,40,
                Action.MOVE_TO,origin,null,purpose),Mode.FOLLOW,false,origin));
            assertEquals("unexpected_move_purpose",BodyRequests.action(new ActionRequest(context,1,request.actionId(),10,40,
                Action.WAIT,null,null,purpose),Mode.FOLLOW,false,origin));
        }
        assertEquals(MovePurpose.INSPECT,request(Action.MOVE_TO,origin,null).movePurpose());
        assertEquals("invalid_move_purpose",BodyRequests.action(new ActionRequest(context,1,UUID.randomUUID(),10,40,
            Action.MOVE_TO,null,null,null),Mode.FOLLOW,false,origin));
    }
    @Test void ownerApproachCanReturnFromOutsideNormalRadiusAndStillRespectsPriorityAndWork() {
        assertNull(BodyRequests.ownerApproach(false,false,Activity.HOLDING,10,12,MovePurpose.OWNER_NEAR,true));
        assertNull(BodyRequests.ownerApproach(false,false,Activity.HOLDING,12,12,MovePurpose.OWNER_FACE,false));
        assertEquals("owner_working",BodyRequests.ownerApproach(false,false,Activity.HOLDING,10,12,MovePurpose.OWNER_FACE,true));
        assertEquals("distance_follow_has_priority",BodyRequests.ownerApproach(false,false,Activity.HOLDING,12.01,12,MovePurpose.OWNER_NEAR,false));
        assertEquals("call_has_priority",BodyRequests.ownerApproach(true,false,Activity.HOLDING,2,12,MovePurpose.OWNER_NEAR,false));
        assertEquals("body_action_busy",BodyRequests.ownerApproach(false,true,Activity.HOLDING,2,12,MovePurpose.OWNER_NEAR,false));
        assertEquals("body_activity_busy",BodyRequests.ownerApproach(false,false,Activity.RETREATING,2,12,MovePurpose.OWNER_NEAR,false));
    }
    @Test void sameActionIdCannotChangeOwnerApproachPurposeOrReplayItsArrival() {
        ControlLease control = new ControlLease(); control.reset(context); control.local(Mode.FOLLOW,false);
        var near = new ActionRequest(context,1,UUID.randomUUID(),10,40,Action.MOVE_TO,null,null,MovePurpose.OWNER_NEAR);
        assertTrue(control.submit(near,10,null).execute());
        assertFalse(control.submit(near,11,null).execute());
        var face = new ActionRequest(context,1,near.actionId(),10,40,Action.MOVE_TO,null,null,MovePurpose.OWNER_FACE);
        assertEquals("action_id_conflict",control.submit(face,11,null).result().reason());
        control.completeAction(near.actionId(),Outcome.SUCCEEDED,"arrived");
        var replay=control.submit(near,12,null);
        assertFalse(replay.execute()); assertEquals(Outcome.SUCCEEDED,replay.result().outcome());
    }

}
