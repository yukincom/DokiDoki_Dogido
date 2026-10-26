package dogido.fabric.protocol;

import com.google.gson.*;
import dogido.fabric.screenshot.BodyViewProtocol;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 通常会話画像は句画像と違いworld変更を越えて配送しない。ACKと新しいsnapshot合図を既存sessionで検査する。 */
class BodyViewSessionTest {
    private static final String ID="view_12345678-1234-1234-1234-123456789abc";
    private DogidoEventClient client(List<String> used) throws Exception { return client(used,new ArrayList<>()); }
    private DogidoEventClient client(List<String> used,List<BodyViewProtocol.Command> captured) throws Exception {
        var c=new DogidoEventClient(org.slf4j.LoggerFactory.getLogger("body-view-test"),
            new DogidoEventClient.ConnectionSettings(true,"http://127.0.0.1:1",""),cmd->{},(cmd,ref)->{},()->false,
            null,null,(cmd,ref)->captured.add(cmd),used::add,(cmd,ref)->{});
        var field=DogidoEventClient.class.getDeclaredField("sessionId");field.setAccessible(true);field.set(c,"session");return c;
    }
    private BodyViewProtocol.State state(DogidoEventClient c) throws Exception {
        var field=DogidoEventClient.class.getDeclaredField("bodyViewState");field.setAccessible(true);return (BodyViewProtocol.State)field.get(c);
    }
    private JsonObject result() {
        JsonObject result=new JsonObject();result.addProperty("command_id",ID);result.addProperty("command_type","capture_body_view");result.addProperty("status","succeeded");
        JsonObject image=new JsonObject();image.addProperty("capture_id",ID);image.addProperty("png_base64","private_image");result.add("body_view",image);return result;
    }
    private void accept(DogidoEventClient c,DogidoEventClient.SessionRef ref,String body) throws Exception {
        var method=DogidoEventClient.class.getDeclaredMethod("handleAcceptedResponse",String.class,DogidoEventClient.SessionRef.class);
        method.setAccessible(true);method.invoke(c,body,ref);
    }
    @Test void afterLookDispatchRequiresCurrentInputGenerationAndDeduplicatesTheCapture() throws Exception {
        List<BodyViewProtocol.Command> captured=new ArrayList<>();var c=client(new ArrayList<>(),captured);var ref=c.currentSession();
        JsonObject request=JsonParser.parseString("{\"type\":\"capture_body_view\",\"command_id\":\""+ID+"\",\"session_id\":\"session\","
            +"\"issued_at\":\"2026-10-26T00:00:01Z\",\"expires_at\":\"2026-10-26T00:00:10Z\","
            +"\"body_entity_uuid\":\"12345678-1234-1234-1234-123456789abc\",\"body_epoch\":\"12345678-1234-1234-1234-123456789abd\","
            +"\"owner_uuid\":\"12345678-1234-1234-1234-123456789abe\",\"dimension\":\"minecraft:overworld\",\"after_motion_sequence\":0,"
            +"\"after_look\":{\"command_id\":\"12345678-1234-1234-1234-123456789abc\",\"input_generation\":6,\"executed_at\":\"2026-10-26T00:00:00Z\"}}").getAsJsonObject();
        JsonObject response=new JsonObject();response.addProperty("body_request_generation",7);JsonArray commands=new JsonArray();
        commands.add(request);response.add("commands",commands);
        accept(c,ref,response.toString());assertTrue(captured.isEmpty());
        request.getAsJsonObject("after_look").addProperty("input_generation",7);
        accept(c,ref,response.toString());assertEquals(1,captured.size());
        accept(c,ref,response.toString());assertEquals(1,captured.size());
        request.addProperty("command_id","view_"+java.util.UUID.randomUUID());response.addProperty("body_request_generation",8);
        accept(c,ref,response.toString());assertEquals(1,captured.size());
        c.invalidateDisplayRequests();request.getAsJsonObject("after_look").addProperty("input_generation",8);
        accept(c,ref,response.toString());assertEquals(1,captured.size());
    }
    @Test void completionRequestsOneFreshSnapshotAndPortalDiscardsPendingImage() throws Exception {
        var c=client(new ArrayList<>());var ref=c.currentSession();assertFalse(c.consumeFreshSnapshotRequest());
        c.rememberBodyViewResult(ref,result());assertEquals(1,state(c).results().size());assertTrue(c.consumeFreshSnapshotRequest());assertFalse(c.consumeFreshSnapshotRequest());
        c.invalidateDisplayRequests();assertTrue(state(c).results().isEmpty());
        c.rememberBodyViewResult(ref,result());assertTrue(state(c).results().isEmpty());assertFalse(c.consumeFreshSnapshotRequest());
    }
    @Test void currentAcknowledgementTouchesTheCaptureOnlyOnce() throws Exception {
        List<String> used=new ArrayList<>();var c=client(used);var ref=c.currentSession();c.rememberBodyViewResult(ref,result());
        String ack="{\"acknowledged_command_ids\":[\""+ID+"\"]}";
        accept(c,ref,ack);assertEquals(List.of(ID),used);assertTrue(state(c).results().isEmpty());
        accept(c,ref,ack);assertEquals(1,used.size());
    }
    @Test void oldSessionAcknowledgementCannotReleaseNewSessionImage() throws Exception {
        List<String> used=new ArrayList<>();var c=client(used);var old=c.currentSession();c.invalidateDisplayRequests();
        c.rememberBodyViewResult(c.currentSession(),result());
        accept(c,old,"{\"acknowledged_command_ids\":[\""+ID+"\"]}");
        assertTrue(used.isEmpty());assertEquals(1,state(c).results().size());
        c.disconnected();assertTrue(state(c).results().isEmpty());assertFalse(c.consumeFreshSnapshotRequest());
    }
}
