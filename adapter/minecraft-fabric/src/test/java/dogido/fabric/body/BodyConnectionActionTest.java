package dogido.fabric.body;

import com.google.gson.JsonObject;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static dogido.fabric.body.BodyInspectProtocol.*;

class BodyConnectionActionTest {
    private final Context context=new Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
    private static final class Source implements BodyConnection.Source {
        int looks,moves,recalls,receiptReads;
        JsonObject lookResults=new JsonObject();
        JsonObject autonomy=BodyInspectProtocol.unavailable("test");
        BodyConnection.RecallResult recall;
        public BodyConnection.Frame capture(UUID owner,String dimension) { return new BodyConnection.Frame(BodyConnection.unavailable("test"),BodyConnection.Hearing.unavailable(),autonomy,lookResults,null); }
        public String lookAt(BodyConnection.LookRequest request) { looks++; return "queued"; }
        public String moveTo(BodyConnection.MoveRequest request) { moves++; return "queued"; }
        public String recall(BodyConnection.RecallRequest request) { recalls++; return "queued"; }
        public BodyConnection.RecallResult recallResult(UUID actionId) { receiptReads++; return recall!=null && actionId.equals(recall.actionId())?recall:null; }
    }
    private BodyConnection connection(Source source) throws Exception {
        var connection=new BodyConnection();
        var field=BodyConnection.class.getDeclaredField("source"); field.setAccessible(true); field.set(connection,source);
        field=BodyConnection.class.getDeclaredField("resolved"); field.setAccessible(true); field.set(connection,true);
        return connection;
    }
    @Test void lookAndMoveUseOneSequenceWithinTheSameEpoch() {
        var connection=new BodyConnection();
        assertEquals(201,connection.nextActionSequence(context,200,100));
        assertEquals(202,connection.nextActionSequence(context,200,100));
        assertEquals(251,connection.nextActionSequence(context,250,105));
        var next=new Context(context.ownerId(),context.bodyId(),UUID.randomUUID(),context.dimension());
        assertEquals(106,connection.nextActionSequence(next,0,105));
    }
    @Test void lookAndMovementAreIndependentWhileRecallStillOwnsItsMovementSlot() throws Exception {
        var source=new Source(); var connection=connection(source); UUID lookId=UUID.randomUUID(),moveId=UUID.randomUUID(),recallId=UUID.randomUUID();
        var look=new BodyConnection.LookRequest(context,201,lookId,100,20,new Point(1,65,2));
        var move=new BodyConnection.MoveRequest(context,202,moveId,100,100,new Point(2,65,3),Purpose.INSPECT);
        var recall=new BodyConnection.RecallRequest(context.ownerId(),UUID.randomUUID(),recallId,context.dimension());
        assertEquals("queued",connection.lookAt(look)); assertEquals("queued",connection.moveTo(move));
        assertEquals("queued",connection.lookAt(look)); // same ID keeps the bridge's idempotent retry contract
        assertEquals("queued",connection.recall(recall)); assertEquals(2,source.looks); assertEquals(1,source.moves); assertEquals(1,source.recalls);
        assertEquals("queued",connection.lookAt(new BodyConnection.LookRequest(context,203,UUID.randomUUID(),100,20,new Point(8,70,0))));
        connection.release(lookId); // a look completion cannot release the concurrent CALL
        assertEquals("body_action_pending",connection.moveTo(move));
        assertEquals("body_action_pending",connection.recall(new BodyConnection.RecallRequest(context.ownerId(),UUID.randomUUID(),UUID.randomUUID(),context.dimension())));
        connection.release(recallId); assertEquals("queued",connection.moveTo(move));
        assertEquals("queued",connection.recall(recall));
        connection.clearActions(); assertEquals("queued",connection.moveTo(move));
    }
    @Test void clearingOrInvalidatingBodyRequestPendingReleasesOnlyItsOwnId() throws Exception {
        var source=new Source(); var connection=connection(source); UUID id=UUID.randomUUID();
        var look=new BodyConnection.LookRequest(context,201,id,100,20,new Point(1,65,2));
        connection.lookAt(look);
        var runtime=new BodyRequestRuntime(connection,(session,generation) -> false,(session,command,sender) -> sender.get(),(session,result) -> {});
        var command=new BodyRequestProtocol.Command(id,java.time.Instant.now(),java.time.Instant.now().plusSeconds(5),4,
            context.ownerId(),context.dimension(),BodyRequestProtocol.Mode.LOOK_PLAYER,context);
        var pendingClass=Class.forName("dogido.fabric.body.BodyRequestRuntime$Pending");
        var constructor=pendingClass.getDeclaredConstructors()[0]; constructor.setAccessible(true);
        var pending=constructor.newInstance(command,new dogido.fabric.protocol.DogidoEventClient.SessionRef("session",1),UUID.randomUUID(),201L,java.time.Instant.now().plusSeconds(35),look);
        var field=BodyRequestRuntime.class.getDeclaredField("pending"); field.setAccessible(true); field.set(runtime,pending);
        runtime.tick(BodyConnection.Frame.unavailable("test")); // stale generation is rejected before touching MinecraftClient
        var move=new BodyConnection.MoveRequest(context,202,UUID.randomUUID(),100,100,new Point(2,65,3),Purpose.INSPECT);
        assertEquals("queued",connection.moveTo(move));
        connection.release(move.actionId()); connection.lookAt(look); field.set(runtime,pending); runtime.clear();
        assertEquals("queued",connection.moveTo(move));
    }
    @Test void aTerminalActionOrUnboundRecallFailureReleasesTheSharedSlot() throws Exception {
        var source=new Source(); var connection=connection(source); UUID id=UUID.randomUUID();
        var look=new BodyConnection.LookRequest(context,201,id,100,20,new Point(1,65,2));
        connection.lookAt(look);
        JsonObject result=new JsonObject(); result.addProperty("action_id",id.toString()); result.addProperty("outcome","succeeded"); source.lookResults.add(id.toString(),result);
        connection.captureFrame(context.ownerId(),context.dimension());
        UUID recallId=UUID.randomUUID(),connectionId=UUID.randomUUID();
        var recall=new BodyConnection.RecallRequest(context.ownerId(),connectionId,recallId,context.dimension());
        assertEquals("queued",connection.recall(recall));
        source.recall=new BodyConnection.RecallResult(context.ownerId(),connectionId,recallId,context.dimension(),null,-1,"rejected","no_safe_spawn_destination");
        connection.captureFrame(context.ownerId(),context.dimension());
        assertEquals("queued",connection.lookAt(new BodyConnection.LookRequest(context,202,UUID.randomUUID(),100,20,new Point(1,65,2))));
    }
    @Test void recallReceiptIsReadOncePerFrameIncludingNotArrivedAndTerminalResults() throws Exception {
        var source=new Source(); var connection=connection(source);
        UUID id=UUID.randomUUID(),connectionId=UUID.randomUUID();
        var request=new BodyConnection.RecallRequest(context.ownerId(),connectionId,id,context.dimension());
        assertEquals("queued",connection.recall(request));
        var empty=connection.captureFrame(context.ownerId(),context.dimension());
        assertNull(connection.recallResult(empty,id)); assertEquals(1,source.receiptReads);
        source.recall=new BodyConnection.RecallResult(context.ownerId(),connectionId,id,context.dimension(),context,7,"accepted","queued");
        var accepted=connection.captureFrame(context.ownerId(),context.dimension());
        assertSame(source.recall,connection.recallResult(accepted,id)); assertEquals(2,source.receiptReads);
        source.recall=new BodyConnection.RecallResult(context.ownerId(),connectionId,id,context.dimension(),null,-1,"rejected","no_safe_spawn_destination");
        var terminal=connection.captureFrame(context.ownerId(),context.dimension());
        assertSame(source.recall,connection.recallResult(terminal,id)); assertEquals(3,source.receiptReads);
        // 別の受付が終端を先に観測して枠を解放しても、未配送の結果は専用台帳から回収する。
        var next=connection.captureFrame(context.ownerId(),context.dimension());
        assertNull(next.recall()); assertEquals(3,source.receiptReads);
        assertSame(source.recall,connection.recallResult(next,id)); assertEquals(4,source.receiptReads);
        assertNull(connection.recallResult(terminal,UUID.randomUUID())); assertEquals(5,source.receiptReads);
    }

}
