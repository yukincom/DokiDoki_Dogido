package dogido.fabric.screenshot;

import com.google.gson.*;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class ScreenshotProtocolTest {
    private JsonObject command() {
        return JsonParser.parseString("""
            {"type":"capture_screenshot","command_id":"shot_165257ef-38d9-4ac3-a32f-5b11badcc4e2",
             "session_id":"sid","poem_id":"session:sid:hk_1","dimension":"minecraft:overworld",
             "issued_at":"2026-10-20T00:00:00Z","expires_at":"2026-10-20T00:00:10Z",
             "body_entity_uuid":"12345678-1234-1234-1234-123456789abc","body_epoch":"12345678-1234-1234-1234-123456789abd",
             "owner_uuid":"12345678-1234-1234-1234-123456789abe"}
            """).getAsJsonObject();
    }
    private List<ScreenshotProtocol.Command> parse(JsonObject c) {
        JsonObject body=new JsonObject();JsonArray arr=new JsonArray();arr.add(c);body.add("commands",arr);
        return ScreenshotProtocol.parseCommands(body.toString());
    }
    @Test void commandHasFixedPoemAndBoundedTimeWithoutSavePath() {
        var c=parse(command()).getFirst();
        assertNull(c.timeError(Instant.parse("2026-10-20T00:00:01Z")));
        assertEquals("expired",c.timeError(c.expiresAt()));
        assertEquals("not_yet_valid",c.timeError(c.issuedAt().minusSeconds(1)));
        var bad=command();bad.addProperty("directory","/elsewhere");assertTrue(parse(bad).isEmpty());
        bad=command();bad.addProperty("poem_id","session:other:hk_1");assertTrue(parse(bad).isEmpty());
        bad=command();bad.addProperty("expires_at","2026-10-20T00:00:11Z");assertTrue(parse(bad).isEmpty());
    }
    @Test void requiresBodyIdentityAndAdvertisesNoPlayerCameraFallback() {
        assertEquals("client.screenshot.body_view.v1",ScreenshotProtocol.CAPABILITY);
        var legacy=command();legacy.remove("body_entity_uuid");legacy.remove("body_epoch");legacy.remove("owner_uuid");
        assertTrue(parse(legacy).isEmpty());
        var invalid=command();invalid.addProperty("body_epoch","short");assertTrue(parse(invalid).isEmpty());
    }
    @Test void captureViewRequiresRestoredCameraAndTheSameFrameBodyContext() {
        var c=parse(command()).getFirst();JsonObject context=new JsonObject();context.addProperty("body_id",c.bodyId().toString());
        context.addProperty("epoch",c.epoch().toString());context.addProperty("owner_id",c.ownerId().toString());context.addProperty("dimension",c.dimension());
        JsonObject view=ScreenshotProtocol.captureView(c,context,true);
        assertEquals("dogido_client_render_eye",view.get("viewpoint").getAsString());assertEquals(c.bodyId().toString(),view.get("body_entity_uuid").getAsString());
        assertFalse(view.has("png_base64"));assertFalse(view.has("body_view"));
        assertThrows(IllegalArgumentException.class,()->ScreenshotProtocol.captureView(c,context,false));
        context.addProperty("epoch",c.ownerId().toString());assertFalse(c.matches(context));
        assertThrows(IllegalArgumentException.class,()->ScreenshotProtocol.captureView(c,context,true));
    }
    @Test void duplicateCommandsAndResultsAreBoundedAndResetDoesNotReshoot() {
        var c=parse(command()).getFirst();var s=new ScreenshotProtocol.State();
        assertTrue(s.claim(c.id()));assertFalse(s.claim(c.id()));
        var r=ScreenshotProtocol.result(c,"succeeded","saved",c.issuedAt());
        s.remember(r);s.remember(r);assertEquals(1,s.results().size());
        s.acknowledge(List.of(c.id()));assertEquals(0,s.results().size());
        s.remember(r);s.clearResults();assertEquals(0,s.results().size());assertFalse(s.claim(c.id()));
    }
    @Test void mergedResultsRespectWireLimitWithoutMutatingQueues() {
        JsonArray ordinary=new JsonArray(),shots=new JsonArray();
        for(int i=0;i<32;i++){ordinary.add("a"+i);shots.add("s"+i);}
        var batch=ScreenshotProtocol.resultBatch(ordinary,shots);
        assertEquals(32,batch.size());assertEquals("a0",batch.get(0).getAsString());
        assertEquals("s0",batch.get(1).getAsString());
        assertEquals(32,ordinary.size());assertEquals(32,shots.size());
    }
}
