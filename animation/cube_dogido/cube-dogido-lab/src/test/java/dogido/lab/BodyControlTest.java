package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.ControlLease;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodyControlTest {
    private Context context() { return new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld"); }
    private StateRequest state(Context c,long revision,long observed,int lease) {
        return new StateRequest(c,revision,0,observed,lease,true,Mode.EXPLORE,false);
    }
    @Test void oldWorldAndOutOfOrderStateCannotReplaceCurrentState() {
        ControlLease lease = new ControlLease(); Context c = context(); lease.reset(c);
        assertEquals(Outcome.ACCEPTED,lease.apply(state(c,2,100,40),100).outcome());
        assertEquals(Outcome.REJECTED,lease.apply(state(c,1,101,40),101).outcome());
        assertEquals(Outcome.REJECTED,lease.apply(state(context(),99,101,40),101).outcome());
        assertEquals(2,lease.revision()); assertEquals(Mode.EXPLORE,lease.mode());
    }
    @Test void advancedObservationDoesNotRequireExactRevisionEquality() {
        ControlLease lease = new ControlLease(); Context c = context(); lease.reset(c);
        assertEquals(Outcome.ACCEPTED,lease.apply(new StateRequest(c,1,3,100,40,true,Mode.FOLLOW,false),110).outcome());
        assertEquals(Outcome.ACCEPTED,lease.apply(new StateRequest(c,2,300,115,40,true,Mode.EXPLORE,false),120).outcome());
    }
    @Test void transportAgeFutureTickAndLeaseLimitsAreChecked() {
        ControlLease lease = new ControlLease(); Context c = context(); lease.reset(c);
        assertEquals(Outcome.REJECTED,lease.apply(state(c,1,1,40),102).outcome());
        assertEquals(Outcome.REJECTED,lease.apply(state(c,1,103,40),102).outcome());
        assertEquals(Outcome.REJECTED,lease.apply(state(c,1,102,201),102).outcome());
        assertEquals(Outcome.ACCEPTED,lease.apply(state(c,1,102,40),102).outcome());
    }
    @Test void oneCallIsNotRestartedByRetryAndCompletionSurvivesUnrelatedReject() {
        ControlLease lease = new ControlLease(); Context c = context(); lease.reset(c); lease.apply(state(c,1,100,100),100);
        ActionRequest action = new ActionRequest(c,1,UUID.randomUUID(),100,40,Action.CALL);
        assertEquals(Outcome.ACCEPTED,lease.request(action,100).outcome());
        assertEquals(Outcome.ACCEPTED,lease.request(action,101).outcome());
        assertEquals(Outcome.REJECTED,lease.request(new ActionRequest(context(),2,UUID.randomUUID(),102,40,Action.CALL),102).outcome());
        lease.completeAction(Outcome.SUCCEEDED,"arrived");
        assertEquals(action.actionId(),lease.actionResult().actionId());
        assertEquals(Outcome.SUCCEEDED,lease.request(action,103).outcome());
    }
    @Test void expiryStopsRemoteExplorationAndAnActiveCall() {
        ControlLease lease = new ControlLease(); Context c = context(); lease.reset(c); lease.apply(state(c,1,100,20),100);
        lease.request(new ActionRequest(c,1,UUID.randomUUID(),100,20,Action.CALL),100);
        lease.expire(120);
        assertFalse(lease.enabled()); assertTrue(lease.expired());
        assertEquals(Outcome.EXPIRED,lease.actionResult().outcome());
    }
    @Test void retreatRefusesCallButNeverMeansBodyDeletion() {
        ControlLease lease = new ControlLease(); Context c = context(); lease.reset(c);
        lease.apply(new StateRequest(c,1,0,100,40,true,Mode.EXPLORE,true),100);
        assertEquals("retreat_has_priority",lease.request(new ActionRequest(c,1,UUID.randomUUID(),100,20,Action.CALL),100).reason());
        assertTrue(lease.enabled()); assertTrue(lease.retreat()); assertEquals(c,lease.context());
    }
    @Test void newEpochInvalidatesOldActionsAndControls() {
        ControlLease lease = new ControlLease(); Context c = context(); lease.reset(c); lease.apply(state(c,1,100,40),100);
        lease.reset(new Context(c.ownerId(),c.bodyId(),UUID.randomUUID(),c.dimension()));
        assertFalse(lease.enabled());
        assertEquals("stale_context",lease.apply(state(c,2,101,40),101).reason());
        assertEquals("stale_context",lease.request(new ActionRequest(c,2,UUID.randomUUID(),101,20,Action.CALL),101).reason());
    }
}
