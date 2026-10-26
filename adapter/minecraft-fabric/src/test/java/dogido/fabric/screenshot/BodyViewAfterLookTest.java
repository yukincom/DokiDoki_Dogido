package dogido.fabric.screenshot;

import com.google.gson.JsonObject;
import dogido.fabric.body.BodyConnection;
import dogido.fabric.body.BodyInspectProtocol.Context;
import dogido.fabric.body.BodyInspectProtocol.Point;
import dogido.fabric.body.BodyRequestProtocol;
import dogido.fabric.body.BodyRequestRuntime;
import dogido.fabric.protocol.DogidoEventClient.SessionRef;
import java.time.Instant;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 明示注視の成功根拠から一枚の予約へ進み、浮遊・補間・新しい操作と遅配を取り違えない回帰検査。 */
class BodyViewAfterLookTest {
    private static final Instant NOW=Instant.parse("2026-10-26T00:00:00Z");
    private static final SessionRef SESSION=new SessionRef("session",3);
    private static final Context CONTEXT=new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
    private static final Point EYE=new Point(0,65,0),TARGET=new Point(0,65,8);
    private BodyRequestProtocol.Command request() {
        return new BodyRequestProtocol.Command(UUID.randomUUID(),NOW,NOW.plusSeconds(5),7,
            CONTEXT.ownerId(),CONTEXT.dimension(),BodyRequestProtocol.Mode.LOOK_DIRECTION,CONTEXT);
    }
    private BodyConnection.LookRequest look(BodyRequestProtocol.Command request) {
        return new BodyConnection.LookRequest(CONTEXT,21,request.commandId(),20,20,TARGET);
    }
    private JsonObject sample() {
        JsonObject sample=new JsonObject(),pose=new JsonObject();sample.add("context",CONTEXT.json());sample.addProperty("server_tick",25);
        pose.add("eye_position",EYE.json());pose.addProperty("view_yaw",0);pose.addProperty("view_pitch",0);sample.add("self_pose",pose);return sample;
    }
    private JsonObject autonomy(BodyRequestProtocol.Command request) {
        JsonObject value=new JsonObject(),action=new JsonObject();value.addProperty("status","observed");value.addProperty("enabled",true);
        value.addProperty("activity","holding");value.addProperty("reason","requested_look");
        action.addProperty("action_id",request.commandId().toString());action.addProperty("sequence",21);
        action.addProperty("outcome","succeeded");action.addProperty("reason","facing_target");value.add("action_result",action);return value;
    }
    private BodyViewProtocol.Command capture(BodyRequestProtocol.Command request) {
        return new BodyViewProtocol.Command("view_"+UUID.randomUUID(),SESSION.sessionId(),NOW.plusSeconds(2),NOW.plusSeconds(10),
            CONTEXT.bodyId(),CONTEXT.epoch(),CONTEXT.ownerId(),CONTEXT.dimension(),0,
            new BodyViewProtocol.AfterLook(request.commandId(),7,NOW.plusSeconds(1)));
    }
    private BodyViewProtocol.Command replace(BodyViewProtocol.Command c,UUID action,long generation,Instant executed,Context context) {
        return new BodyViewProtocol.Command(c.id(),c.sessionId(),c.issuedAt(),c.expiresAt(),context.bodyId(),context.epoch(),
            context.ownerId(),context.dimension(),c.afterMotionSequence(),new BodyViewProtocol.AfterLook(action,generation,executed));
    }
    private BodyViewAfterLook completed(BodyRequestProtocol.Command request) {
        var state=new BodyViewAfterLook();state.completed(SESSION,request,look(request),sample(),autonomy(request),NOW.plusSeconds(1));return state;
    }
    @Test void oneVerifiedLookBindsOneCaptureAndRejectsForeignReceiptFields() {
        var request=request();var state=completed(request);var capture=capture(request);
        var otherContext=new Context(CONTEXT.ownerId(),CONTEXT.bodyId(),UUID.randomUUID(),CONTEXT.dimension());
        assertNull(state.capture(replace(capture,UUID.randomUUID(),7,NOW.plusSeconds(1),CONTEXT),SESSION,NOW.plusSeconds(2)));
        assertNull(state.capture(replace(capture,request.commandId(),8,NOW.plusSeconds(1),CONTEXT),SESSION,NOW.plusSeconds(2)));
        assertNull(state.capture(replace(capture,request.commandId(),7,NOW,CONTEXT),SESSION,NOW.plusSeconds(2)));
        assertNull(state.capture(replace(capture,request.commandId(),7,NOW.plusSeconds(1),otherContext),SESSION,NOW.plusSeconds(2)));
        assertNull(state.capture(capture,new SessionRef("other",3),NOW.plusSeconds(2)));
        assertNull(state.capture(capture,new SessionRef("session",4),NOW.plusSeconds(2)));
        var proof=state.capture(capture,SESSION,NOW.plusSeconds(2));assertNotNull(proof);
        assertSame(proof,state.capture(capture,SESSION,NOW.plusSeconds(3))); // 同じ撮影の描画前・保存後の照合
        assertNull(state.capture(capture(request),SESSION,NOW.plusSeconds(3))); // 同じ注視で別の一枚を作らない
        assertNull(state.capture(capture,SESSION,NOW.plusSeconds(10)));
    }
    @Test void failedOrQueuedNativeResultsNeverAuthorizeAnImage() {
        for(String outcome:new String[]{"accepted","rejected","interrupted","expired"}) {
            var request=request();var state=new BodyViewAfterLook();JsonObject a=autonomy(request);
            a.getAsJsonObject("action_result").addProperty("outcome",outcome);
            state.completed(SESSION,request,look(request),sample(),a,NOW.plusSeconds(1));
            assertNull(state.capture(capture(request),SESSION,NOW.plusSeconds(2)),outcome);
        }
        var request=request();var state=new BodyViewAfterLook();JsonObject a=autonomy(request);
        a.getAsJsonObject("action_result").addProperty("reason","arrived");
        state.completed(SESSION,request,look(request),sample(),a,NOW.plusSeconds(1));
        assertNull(state.capture(capture(request),SESSION,NOW.plusSeconds(2)));
    }
    @Test void currentSnapshotMustStillHoldTheSameCompletedAction() {
        var request=request();var state=completed(request);var proof=state.capture(capture(request),SESSION,NOW.plusSeconds(2));
        assertTrue(proof.matches(sample(),autonomy(request)));
        for(String field:new String[]{"activity","reason","enabled","action_id","sequence","outcome","context","server_tick"}) {
            JsonObject s=sample(),a=autonomy(request),action=a.getAsJsonObject("action_result");
            switch(field) {
                case "activity" -> a.addProperty(field,"following");
                case "reason" -> a.addProperty(field,"following_owner");
                case "enabled" -> a.addProperty(field,false);
                case "action_id" -> action.addProperty(field,UUID.randomUUID().toString());
                case "sequence" -> action.addProperty(field,22);
                case "outcome" -> action.addProperty(field,"accepted");
                case "context" -> s.getAsJsonObject(field).addProperty("epoch",UUID.randomUUID().toString());
                case "server_tick" -> s.addProperty(field,24);
            }
            assertFalse(proof.matches(s,a),field);
        }
    }
    @Test void hoveringDoesNotBlockTheCompletedLookButInterpolatedWrongFacingAndTravelDo() {
        var request=request();var state=completed(request);var proof=state.capture(capture(request),SESSION,NOW.plusSeconds(2));
        var ordinary=new BodyViewMotion();
        for(int tick=0;tick<90;tick++) {
            double y=65+.18*Math.sin(tick*Math.PI*2/120);
            double pitch=Math.toDegrees(Math.atan2(y-65,8));
            var rendered=new BodyViewAfterLook.Pose(new Point(0,y,0),0,pitch);
            ordinary.observe("world",new BodyViewMotion.Pose(0,y,0,0,pitch));
            assertTrue(proof.facing(rendered));
            assertFalse(ordinary.still(new BodyViewMotion.Pose(0,y,0,0,pitch)));
        }
        assertFalse(proof.facing(new BodyViewAfterLook.Pose(EYE,30,0))); // 補間中の旧方向
        assertFalse(proof.facing(new BodyViewAfterLook.Pose(new Point(.03,65,0),0,0)));
        assertFalse(proof.facing(new BodyViewAfterLook.Pose(new Point(0,65.5,0),0,0)));
    }
    @Test void repeatedSameDirectionIsAnotherCaptureOnlyAfterAnotherActualSuccess() {
        var first=request();var state=completed(first);var original=capture(first);var motion=new BodyViewMotion();
        assertNotNull(state.capture(original,SESSION,NOW.plusSeconds(2)));assertEquals(1,motion.afterLookSequence(0));
        state.clear();assertNull(state.capture(original,SESSION,NOW.plusSeconds(2)));
        var second=request();state.completed(SESSION,second,look(second),sample(),autonomy(second),NOW.plusSeconds(1));
        assertNull(state.capture(original,SESSION,NOW.plusSeconds(2)));
        assertNotNull(state.capture(capture(second),SESSION,NOW.plusSeconds(2)));assertEquals(2,motion.afterLookSequence(1));
        assertEquals(6,motion.afterLookSequence(5));assertEquals(7,motion.afterLookSequence(1));
        assertThrows(IllegalStateException.class,()->motion.afterLookSequence(Long.MAX_VALUE));
        assertEquals(7,motion.sequence());
    }
    @Test void changingInputGenerationInvalidatesAnAlreadyBoundCapture() throws Exception {
        var generation=new AtomicLong(7);
        var runtime=new BodyRequestRuntime(new BodyConnection(),(session,input)->SESSION.equals(session) && generation.get()==input,
            (session,command,sender)->"queued",(session,result)->{});
        var field=BodyRequestRuntime.class.getDeclaredField("lookCaptures");field.setAccessible(true);
        var state=(BodyViewAfterLook)field.get(runtime);
        Instant now=Instant.now();
        var request=new BodyRequestProtocol.Command(UUID.randomUUID(),now.minusSeconds(1),now.plusSeconds(4),7,
            CONTEXT.ownerId(),CONTEXT.dimension(),BodyRequestProtocol.Mode.LOOK_TARGET,CONTEXT);
        state.completed(SESSION,request,look(request),sample(),autonomy(request),now);
        var c=new BodyViewProtocol.Command("view_"+UUID.randomUUID(),SESSION.sessionId(),now,now.plusSeconds(10),
            CONTEXT.bodyId(),CONTEXT.epoch(),CONTEXT.ownerId(),CONTEXT.dimension(),0,new BodyViewProtocol.AfterLook(request.commandId(),7,now));
        assertNotNull(runtime.lookCapture(c,SESSION));generation.set(8);
        assertNull(runtime.lookCapture(c,SESSION));assertNull(runtime.lookCapture(c,new SessionRef("session",4)));
    }
    @Test void delayedOrdinaryReservationsCannotReplaceOrRepeatTheExplicitCapture() {
        var request=request();var explicit=capture(request);var order=new BodyViewRequestOrder();
        var old=new BodyViewProtocol.Command("view_"+UUID.randomUUID(),"session",NOW,NOW.plusSeconds(10),
            CONTEXT.bodyId(),CONTEXT.epoch(),CONTEXT.ownerId(),CONTEXT.dimension(),0);
        assertTrue(order.accept(old,false));assertTrue(order.accept(explicit,false));
        assertFalse(order.accept(old,true));assertFalse(order.accept(old,false));
        var next=new BodyViewProtocol.Command("view_"+UUID.randomUUID(),"session",NOW.plusSeconds(3),NOW.plusSeconds(10),
            CONTEXT.bodyId(),CONTEXT.epoch(),CONTEXT.ownerId(),CONTEXT.dimension(),1);
        assertFalse(order.accept(next,true));assertTrue(order.accept(next,false));
    }
}
