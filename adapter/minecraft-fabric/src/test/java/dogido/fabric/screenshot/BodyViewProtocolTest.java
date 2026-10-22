package dogido.fabric.screenshot;

import com.google.gson.*;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodyViewProtocolTest {
    private static final String ID="view_12345678-1234-1234-1234-123456789abc";
    private static final Instant NOW=Instant.parse("2026-10-21T00:00:00Z");
    private JsonObject command() {
        JsonObject c=new JsonObject();c.addProperty("type","capture_body_view");c.addProperty("command_id",ID);c.addProperty("session_id","session-a");
        c.addProperty("issued_at",NOW.toString());c.addProperty("expires_at",NOW.plusSeconds(10).toString());
        c.addProperty("body_entity_uuid","12345678-1234-1234-1234-123456789abc");c.addProperty("body_epoch","12345678-1234-1234-1234-123456789abd");
        c.addProperty("owner_uuid","12345678-1234-1234-1234-123456789abe");c.addProperty("dimension","minecraft:overworld");c.addProperty("after_motion_sequence",0);return c;
    }
    private List<BodyViewProtocol.Command> parse(JsonObject c) {JsonObject response=new JsonObject();JsonArray commands=new JsonArray();commands.add(c);response.add("commands",commands);return BodyViewProtocol.parseCommands(response.toString());}
    @Test void keepsExistingWireNamesAndRequiresCompleteBodyIdentity() {
        var c=parse(command()).getFirst();assertEquals(ID,c.id());assertNull(c.timeError(NOW));assertEquals("expired",c.timeError(NOW.plusSeconds(10)));
        JsonObject context=new JsonObject();context.addProperty("body_id",c.bodyId().toString());context.addProperty("owner_id",c.ownerId().toString());
        context.addProperty("epoch",c.epoch().toString());context.addProperty("dimension",c.dimension());assertTrue(c.matches(context));
        context.addProperty("epoch",c.bodyId().toString());assertFalse(c.matches(context));
        JsonObject result=BodyViewProtocol.result(c,"failed","expired",NOW);assertEquals("capture_body_view",result.get("command_type").getAsString());assertFalse(result.has("body_view"));
    }
    @Test void rejectsOpenEndedOrForeignCommands() {
        JsonObject c=command();c.addProperty("destination","/tmp/personal.png");assertTrue(parse(c).isEmpty());
        c=command();c.addProperty("expires_at",NOW.plusSeconds(11).toString());assertTrue(parse(c).isEmpty());
        c=command();c.addProperty("body_epoch","short");assertTrue(parse(c).isEmpty());
        c=command();c.addProperty("type","capture_screenshot");assertTrue(parse(c).isEmpty());
        c=command();c.addProperty("command_id","view_../private");assertTrue(parse(c).isEmpty());
    }
    @Test void rejectsMissingNegativeAndFractionalStopSequence() {
        JsonObject c=command();c.remove("after_motion_sequence");assertTrue(parse(c).isEmpty());
        for(double value:new double[]{-1,.5}) {c=command();c.addProperty("after_motion_sequence",value);assertTrue(parse(c).isEmpty());}
    }
    private JsonObject success(String id) {
        JsonObject r=BodyViewProtocol.result(parse(command()).getFirst(),"succeeded","captured",NOW);r.addProperty("command_id",id);
        JsonObject image=new JsonObject();image.addProperty("capture_id",ID);image.addProperty("png_base64","image");r.add("body_view",image);return r;
    }
    @Test void deduplicatesAndReleasesImageOnlyOnAcknowledgement() {
        var state=new BodyViewProtocol.State();assertTrue(state.claim(ID));assertFalse(state.claim(ID));state.remember(success(ID));
        assertTrue(state.acknowledge(List.of("unrelated")).isEmpty());assertEquals(1,state.results().size());
        assertEquals(List.of(ID),state.acknowledge(List.of(ID)));assertTrue(state.results().isEmpty());
        state.remember(success(ID));state.clearResults();assertTrue(state.results().isEmpty());assertFalse(state.claim(ID));
    }
    @Test void batchesOneImageAndAtMost32TotalWithoutDroppingOtherLedgerItems() {
        JsonArray ordinary=new JsonArray(),captures=new JsonArray();
        for(int i=0;i<40;i++){JsonObject r=new JsonObject();r.addProperty("command_id","ordinary-"+i);ordinary.add(r);}
        captures.add(success(ID));captures.add(success("second"));
        JsonArray batch=BodyViewProtocol.resultBatch(ordinary,captures);assertEquals(32,batch.size());
        assertTrue(batch.get(0).getAsJsonObject().has("body_view"));assertEquals("ordinary-30",batch.get(31).getAsJsonObject().get("command_id").getAsString());
        assertEquals(40,ordinary.size());assertEquals(2,captures.size());
    }
    @Test void eventBudgetDropsOnlyImageAndKeepsAUsableFailureReceipt() {
        JsonObject outbound=new JsonObject();String observation="観測".repeat(25_000);outbound.addProperty("observation",observation);
        JsonObject image=success(ID);image.getAsJsonObject("body_view").addProperty("png_base64","A".repeat(160_000));
        JsonArray results=new JsonArray();results.add(image);outbound.add("command_results",results);
        var failed=new java.util.ArrayList<JsonObject>();String encoded=BodyViewProtocol.encodeEvent(outbound,new Gson(),failed::add);
        assertTrue(encoded.getBytes(java.nio.charset.StandardCharsets.UTF_8).length<=BodyViewProtocol.MAX_EVENT_BYTES);
        assertEquals(observation,outbound.get("observation").getAsString());assertEquals(1,failed.size());
        assertEquals("payload_too_large",failed.getFirst().get("detail_code").getAsString());assertFalse(encoded.contains("png_base64"));
    }
    @Test void delayedAcknowledgementStillTouchesCaptureAfterAnotherEventNeededFailure() {
        var state=new BodyViewProtocol.State();state.remember(success(ID));
        JsonObject failure=success(ID);failure.remove("body_view");failure.addProperty("status","failed");state.remember(failure);
        assertEquals(List.of(ID),state.acknowledge(List.of(ID)));assertTrue(state.results().isEmpty());
    }
}
