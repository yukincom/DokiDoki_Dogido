package dogido.fabric.protocol;

import com.google.gson.JsonObject;
import dogido.fabric.screenshot.ScreenshotProtocol;
import java.time.Instant;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 撮影開始はworld epoch、保存済み結果の配送は元sessionで区切る。 */
class ScreenshotSessionTest {
    private DogidoEventClient client() throws Exception {
        var c=new DogidoEventClient(org.slf4j.LoggerFactory.getLogger("screenshot-test"),
            new DogidoEventClient.ConnectionSettings(true,"http://127.0.0.1:1",""),cmd->{},(cmd,ref)->{},()->false);
        session(c,"session");return c;
    }
    private void session(DogidoEventClient c,String id) throws Exception {
        var field=DogidoEventClient.class.getDeclaredField("sessionId");field.setAccessible(true);field.set(c,id);
    }
    private int pending(DogidoEventClient c) throws Exception {
        var field=DogidoEventClient.class.getDeclaredField("screenshotState");field.setAccessible(true);
        return ((ScreenshotProtocol.State)field.get(c)).results().size();
    }
    private JsonObject result() {
        return ScreenshotProtocol.result(new ScreenshotProtocol.Command("shot_id","session","session:session:hk",
            "minecraft:overworld",Instant.now(),Instant.now().plusSeconds(10),
            java.util.UUID.fromString("12345678-1234-1234-1234-123456789abc"),
            java.util.UUID.fromString("12345678-1234-1234-1234-123456789abd"),
            java.util.UUID.fromString("12345678-1234-1234-1234-123456789abe")),"succeeded","saved",Instant.now());
    }
    @Test void dimensionChangeKeepsSavedReceiptButDisconnectDiscardsIt() throws Exception {
        var c=client();var ref=c.currentSession();
        c.rememberScreenshotResult(ref,result());c.invalidateDisplayRequests();
        assertFalse(c.isCurrent(ref));assertEquals(1,pending(c));
        c.disconnected();assertEquals(0,pending(c));
        session(c,"another");c.rememberScreenshotResult(ref,result());assertEquals(0,pending(c));
    }
    @Test void saveCallbackAfterPortalStillBelongsToOriginalPoem() throws Exception {
        var c=client();var ref=c.currentSession();c.invalidateDisplayRequests();
        c.rememberScreenshotResult(ref,result());assertEquals(1,pending(c));
    }
}
