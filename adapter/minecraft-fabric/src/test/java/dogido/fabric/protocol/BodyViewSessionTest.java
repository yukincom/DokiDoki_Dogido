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
    private DogidoEventClient client(List<String> used) throws Exception {
        var c=new DogidoEventClient(org.slf4j.LoggerFactory.getLogger("body-view-test"),
            new DogidoEventClient.ConnectionSettings(true,"http://127.0.0.1:1",""),cmd->{},(cmd,ref)->{},()->false,
            null,null,(cmd,ref)->{},used::add);
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
