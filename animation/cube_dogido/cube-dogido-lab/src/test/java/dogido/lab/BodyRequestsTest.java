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
}
