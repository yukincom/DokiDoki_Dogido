package dogido.fabric.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dogido.fabric.protocol.DogidoEventClient.SessionRef;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static dogido.fabric.body.BodyInspectProtocol.*;

/** モデル・Minecraftを使わず、候補世代と実行結果の配送境界を検証する。 */
class BodyInspectControllerTest {
    static final Instant NOW=Instant.parse("2026-10-14T00:00:00Z");
    static final Context CONTEXT=new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
    static final SessionRef SESSION=new SessionRef("session",1);
    static final UUID ENTITY=UUID.randomUUID();
    static final Target TARGET=new Target("e:"+ENTITY,"entity","minecraft:cow","Cow",new Point(8,64,0),ENTITY);
    static Command command() { return new Command(UUID.randomUUID(),NOW,NOW.plusSeconds(4),CONTEXT,TARGET); }
    static JsonObject observation() {
        JsonObject row=new JsonObject(); row.addProperty("version",2); row.addProperty("status","observed");
        row.add("context",CONTEXT.json()); row.addProperty("server_tick",100); row.addProperty("snapshot_revision",200);
        row.addProperty("follow_distance",8); row.addProperty("age_ms",0); row.addProperty("enabled",true); row.addProperty("mode","follow");
        row.addProperty("warp_distance",24);
        var feeling=new JsonObject(); feeling.addProperty("value",100); feeling.addProperty("owner_distance",0);
        feeling.addProperty("owner_visible",true); feeling.addProperty("face_visible",true); feeling.addProperty("owner_working",false);
        row.add("reassurance",feeling);
        JsonArray candidates=new JsonArray(); candidates.add(TARGET.json()); row.add("candidates",candidates); return row;
    }
    static class Fake implements BodyInspectController.Access {
        JsonObject observation=observation(); Point actual=TARGET.position(); Point owner=new Point(0,64,0);
        String submission="queued"; boolean current=true; boolean clear=true; Target grounded=TARGET;
        Instant clockNow=NOW;
        Runnable onCapture=() -> {}, onResolve=() -> {}, onCollision=() -> {};
        java.time.Clock clock=new java.time.Clock() {
            public java.time.ZoneId getZone() { return java.time.ZoneOffset.UTC; }
            public java.time.Clock withZone(java.time.ZoneId zone) { return this; }
            public Instant instant() { return clockNow; }
        };
        BodyApproachGeometry.Bounds shape=new BodyApproachGeometry.Bounds(-.4,0,-.4,.4,.8,.4);
        BodyApproachGeometry.Bounds targetBounds=new BodyApproachGeometry.Bounds(7.5,64,-.5,8.5,65,.5);
        List<BodyConnection.MoveRequest> requests=new ArrayList<>(); List<JsonObject> results=new ArrayList<>();
        BodyInspectController controller=new BodyInspectController(this,ref -> current && SESSION.equals(ref),results::add,clock);
        public BodyInspectController.Live capture() { onCapture.run(); return new BodyInspectController.Live(observation,new Point(0,64,0),owner,1.62); }
        public BodyInspectController.ResolvedTarget resolve(Target target,BodyInspectController.Live live) {
            onResolve.run();
            return actual == null ? null : new BodyInspectController.ResolvedTarget(actual,targetBounds,shape,false);
        }
        public Target imageTarget(Command command,SessionRef session,BodyInspectController.Live live) {return grounded;}
        public boolean collisionFree(BodyApproachGeometry.Bounds destination) { onCollision.run(); return clear; }
        public String moveTo(SessionRef session,Command command,BodyConnection.MoveRequest request) { requests.add(request); return submission; }
        void outcome(Command command,String outcome) {
            JsonObject result=new JsonObject(); result.addProperty("action_id",command.commandId().toString());
            result.addProperty("sequence",requests.getFirst().sequence()); result.addProperty("outcome",outcome);
            result.addProperty("reason","arrived"); observation.add("action_result",result);
        }
        String detail() { return results.getFirst().get("detail_code").getAsString(); }
    }
    @Test void queuedAndAcceptedAreNotArrivalAndRepeatedCommandDoesNotMoveAgain() {
        Fake fake=new Fake(); Command command=command();
        fake.controller.handle(command,SESSION,NOW); fake.controller.handle(command,SESSION,NOW);
        assertEquals(1,fake.requests.size()); assertTrue(fake.results.isEmpty());
        assertEquals(command.commandId(),fake.requests.getFirst().actionId());
        assertFalse(fake.shape.at(fake.requests.getFirst().destination()).intersects(fake.targetBounds.expand(.2)));
        fake.outcome(command,"accepted"); fake.controller.tick(NOW.plusMillis(100));
        assertTrue(fake.results.isEmpty()); assertTrue(fake.controller.pending());
        fake.outcome(command,"succeeded"); fake.controller.tick(NOW.plusMillis(150));
        assertFalse(fake.controller.pending()); assertEquals("succeeded",fake.results.getFirst().get("status").getAsString());
        fake.controller.tick(NOW.plusMillis(200)); assertEquals(1,fake.results.size());
        fake.controller.handle(command,SESSION,NOW.plusMillis(200)); assertEquals(1,fake.requests.size());
    }
    @Test void wrongActionOrSequenceDoesNotConfirmArrivalAndWaitIsBounded() {
        Fake fake=new Fake(); Command command=command(); fake.controller.handle(command,SESSION,NOW);
        fake.outcome(command,"succeeded"); fake.observation.getAsJsonObject("action_result").addProperty("sequence",0);
        fake.controller.tick(NOW.plusSeconds(1)); assertTrue(fake.results.isEmpty());
        fake.outcome(command,"succeeded"); fake.observation.getAsJsonObject("action_result").addProperty("action_id",UUID.randomUUID().toString());
        fake.controller.tick(NOW.plusSeconds(2)); assertTrue(fake.results.isEmpty());
        fake.controller.tick(NOW.plusSeconds(35)); assertEquals("result_timeout",fake.detail()); assertFalse(fake.controller.pending());
    }
    @Test void delayedDeliveryDoesNotExtendTheIssuedDeadline() {
        Fake fake=new Fake(); Command command=command(); fake.clockNow=NOW.plusSeconds(3); fake.controller.handle(command,SESSION,fake.clockNow);
        assertEquals(1,fake.requests.size());
        fake.controller.tick(NOW.plusSeconds(34)); assertTrue(fake.results.isEmpty());
        fake.outcome(command,"succeeded"); fake.controller.tick(NOW.plusSeconds(35));
        assertEquals("result_timeout",fake.detail()); assertEquals("expired",fake.results.getFirst().get("status").getAsString());
    }
    @Test void staleSnapshotDisabledAndDirectNeverSubmit() {
        for (String invalid : List.of("age","enabled","mode","context","candidates")) {
            Fake fake=new Fake();
            switch (invalid) {
                case "age" -> fake.observation.addProperty("age_ms",3_000);
                case "enabled" -> fake.observation.addProperty("enabled",false);
                case "mode" -> fake.observation.addProperty("mode","direct");
                case "context" -> fake.observation.getAsJsonObject("context").addProperty("epoch",UUID.randomUUID().toString());
                case "candidates" -> fake.observation.add("candidates",new JsonArray());
            }
            fake.controller.handle(command(),SESSION,NOW);
            assertTrue(fake.requests.isEmpty(),invalid); assertEquals("rejected",fake.results.getFirst().get("status").getAsString());
        }
    }
    @Test void unresolvedCurrentTargetDoesNotSubmit() {
        Fake fake=new Fake(); fake.actual=null; fake.controller.handle(command(),SESSION,NOW);
        assertEquals("target_unavailable",fake.detail()); assertTrue(fake.requests.isEmpty());
    }
    @Test void movedCandidateOrMovedActualTargetDoesNotSubmit() {
        Fake cached=new Fake(); cached.observation.getAsJsonArray("candidates").get(0).getAsJsonObject().add("position",new Point(11,64,0).json());
        cached.controller.handle(command(),SESSION,NOW); assertEquals("target_moved",cached.detail());
        Fake live=new Fake(); live.actual=new Point(11,64,0); live.controller.handle(command(),SESSION,NOW);
        assertEquals("target_moved",live.detail()); assertTrue(live.requests.isEmpty());
    }
    @Test void expiredOrFutureCommandDoesNotSubmit() {
        Fake expired=new Fake(); expired.controller.handle(command(),SESSION,NOW.plusSeconds(4));
        assertEquals("expired",expired.detail()); assertTrue(expired.requests.isEmpty());
        Fake future=new Fake(); future.controller.handle(command(),SESSION,NOW.minusMillis(1));
        assertEquals("issued_in_future",future.detail()); assertTrue(future.requests.isEmpty());
    }
    @Test void sessionOrWorldEpochChangeDiscardsPendingAndStaleCallback() {
        Fake fake=new Fake(); Command command=command(); fake.controller.handle(command,SESSION,NOW);
        fake.current=false; fake.controller.tick(NOW.plusMillis(100));
        assertFalse(fake.controller.pending()); assertTrue(fake.results.isEmpty());
        fake.controller.handle(command(),new SessionRef("session",2),NOW); assertEquals(1,fake.requests.size());
    }
    @Test void sessionChangeDuringCaptureResolveOrCollisionNeverSubmits() {
        for (String phase : List.of("capture","resolve","collision")) {
            Fake fake=new Fake(); Runnable replaceSession=() -> fake.current=false;
            switch (phase) {
                case "capture" -> fake.onCapture=replaceSession;
                case "resolve" -> fake.onResolve=replaceSession;
                case "collision" -> fake.onCollision=replaceSession;
            }
            fake.controller.handle(command(),SESSION,NOW);
            assertTrue(fake.requests.isEmpty(),phase); assertFalse(fake.controller.pending()); assertTrue(fake.results.isEmpty());
        }
    }
    @Test void expiryDuringResolveOrCollisionUsesTheActualClockBeforeSubmission() {
        for (String phase : List.of("resolve","collision")) {
            Fake fake=new Fake(); Runnable exceedDeadline=() -> fake.clockNow=NOW.plusSeconds(4);
            if (phase.equals("resolve")) fake.onResolve=exceedDeadline; else fake.onCollision=exceedDeadline;
            fake.controller.handle(command(),SESSION,NOW);
            assertTrue(fake.requests.isEmpty(),phase); assertEquals("expired",fake.detail()); assertFalse(fake.controller.pending());
            assertEquals(fake.clockNow.toString(),fake.results.getFirst().get("executed_at").getAsString());
        }
    }
    @Test void finalSubmissionGuardCanRejectASessionSwitchAfterTheControllerCheck() {
        Fake fake=new Fake(); fake.submission="stale_session"; fake.controller.handle(command(),SESSION,NOW);
        assertFalse(fake.controller.pending()); assertTrue(fake.results.isEmpty());
    }
    @Test void targetMovementUsesTheSameOneAndAHalfBlockLimitAsRust() {
        Fake fake=new Fake(); fake.actual=new Point(9.51,64,0); fake.controller.handle(command(),SESSION,NOW);
        assertTrue(fake.requests.isEmpty()); assertEquals("target_moved",fake.detail());
    }
    @Test void bodyContextChangeFinishesOldActionWithoutAcceptingNewBodyResult() {
        Fake fake=new Fake(); Command command=command(); fake.controller.handle(command,SESSION,NOW);
        fake.outcome(command,"succeeded"); fake.observation.getAsJsonObject("context").addProperty("body_id",UUID.randomUUID().toString());
        fake.controller.tick(NOW.plusMillis(100)); assertEquals("stale_context",fake.detail());
    }
    @Test void refusedSubmissionFinishesWithoutArrivalAndCompetingCommandIsRejected() {
        Fake refused=new Fake(); refused.submission="stale_context"; refused.controller.handle(command(),SESSION,NOW);
        assertEquals("stale_context",refused.detail()); assertFalse(refused.controller.pending());
        Fake busy=new Fake(); busy.controller.handle(command(),SESSION,NOW); busy.controller.handle(command(),SESSION,NOW);
        assertEquals(1,busy.requests.size()); assertEquals("body_action_pending",busy.detail());
    }
    @Test void ownerRangeAndExistingSequenceAreRechecked() {
        Fake far=new Fake(); far.owner=new Point(-9,64,0); far.controller.handle(command(),SESSION,NOW);
        assertEquals("outside_owner_range",far.detail()); assertTrue(far.requests.isEmpty());
        Fake fake=new Fake(); JsonObject result=new JsonObject(); result.addProperty("sequence",1000); fake.observation.add("action_result",result);
        fake.controller.handle(command(),SESSION,NOW); assertEquals(1001,fake.requests.getFirst().sequence());
    }
    @Test void noFreeApproachDoesNotSubmit() {
        Fake fake=new Fake(); fake.clear=false; fake.controller.handle(command(),SESSION,NOW);
        assertTrue(fake.requests.isEmpty()); assertEquals("no_safe_approach",fake.detail());
    }
    @Test void allTerminalOutcomesAreReportedOnce() {
        for (String outcome : List.of("succeeded","rejected","interrupted","expired")) {
            Fake fake=new Fake(); Command command=command(); fake.controller.handle(command,SESSION,NOW); fake.outcome(command,outcome);
            fake.controller.tick(NOW.plusMillis(100)); fake.controller.tick(NOW.plusMillis(200));
            assertEquals(1,fake.results.size()); assertEquals(outcome.equals("interrupted") ? "failed" : outcome,fake.results.getFirst().get("status").getAsString());
        }
    }
    @Test void updatedFollowRadiusControlsTargetAndDestinationBeforeSubmission() {
        Fake within=new Fake(); within.controller.handle(command(),SESSION,NOW);
        assertEquals(1,within.requests.size()); assertTrue(within.requests.getFirst().destination().distance(within.owner) <= 8);
        Fake outside=new Fake(); outside.owner=new Point(-.01,64,0); outside.controller.handle(command(),SESSION,NOW);
        assertTrue(outside.requests.isEmpty()); assertEquals("outside_owner_range",outside.detail());
        Fake configured=new Fake(); configured.owner=new Point(-2,64,0); configured.observation.addProperty("follow_distance",12);
        configured.controller.handle(command(),SESSION,NOW); assertEquals(1,configured.requests.size());
    }
    @Test void ownerPurposeSharesDeliveryAndAcknowledgementWithoutAnAmbientTarget() {
        for (Purpose purpose : List.of(Purpose.OWNER_NEAR,Purpose.OWNER_FACE)) {
            Fake fake=new Fake(); fake.owner=new Point(10,64,0);
            fake.observation.add("candidates",new JsonArray()); fake.actual=null;
            Command command=new Command(UUID.randomUUID(),NOW,NOW.plusSeconds(4),CONTEXT,null,purpose);
            fake.controller.handle(command,SESSION,NOW);
            assertEquals(1,fake.requests.size()); assertEquals(purpose,fake.requests.getFirst().purpose());
            assertNull(fake.requests.getFirst().destination()); assertTrue(fake.results.isEmpty());
            fake.outcome(command,"accepted"); fake.controller.tick(NOW.plusMillis(100)); assertTrue(fake.results.isEmpty());
            fake.outcome(command,"succeeded"); fake.controller.tick(NOW.plusMillis(200)); assertEquals("arrived",fake.detail());
        }
    }
    @Test void ownerFaceRechecksWorkingAndEmergencyRangeWhileNearCanStayBesideWork() {
        for (Purpose purpose : List.of(Purpose.OWNER_NEAR,Purpose.OWNER_FACE)) {
            Fake fake=new Fake(); fake.observation.getAsJsonObject("reassurance").addProperty("owner_working",true);
            var command=new Command(UUID.randomUUID(),NOW,NOW.plusSeconds(4),CONTEXT,null,purpose);
            fake.controller.handle(command,SESSION,NOW);
            if (purpose == Purpose.OWNER_FACE) { assertEquals("owner_working",fake.detail()); assertTrue(fake.requests.isEmpty()); }
            else assertEquals(1,fake.requests.size());
        }
        Fake fake=new Fake(); fake.owner=new Point(12.01,64,0);
        fake.controller.handle(new Command(UUID.randomUUID(),NOW,NOW.plusSeconds(4),CONTEXT,null,Purpose.OWNER_NEAR),SESSION,NOW);
        assertEquals("outside_owner_range",fake.detail()); assertTrue(fake.requests.isEmpty());
    }
    @Test void imageCanResolveOutsideCandidateTableButStillNeedsSafeCurrentTarget() {
        var image=new ImageTarget("view_12345678-1234-1234-1234-123456789abc","気になる牛","mob","any","center","middle");
        for(String mode:List.of("valid","unresolved","unsafe","moved","range")) {
            var fake=new Fake();fake.observation.add("candidates",new JsonArray());
            if(mode.equals("unresolved"))fake.grounded=null;
            if(mode.equals("unsafe"))fake.clear=false;
            if(mode.equals("moved"))fake.actual=new Point(14,64,0);
            if(mode.equals("range"))fake.owner=new Point(-10,64,0);
            var command=new Command(UUID.randomUUID(),NOW,NOW.plusSeconds(4),CONTEXT,null,Purpose.INSPECT,image);
            fake.controller.handle(command,SESSION,NOW);
            assertEquals(mode.equals("valid")?1:0,fake.requests.size(),mode);
            if(mode.equals("valid"))assertEquals(Purpose.INSPECT,fake.requests.getFirst().purpose());
        }
    }

}
