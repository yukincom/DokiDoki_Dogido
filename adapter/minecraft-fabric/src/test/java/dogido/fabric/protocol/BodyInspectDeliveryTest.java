package dogido.fabric.protocol;

import com.google.gson.JsonObject;
import java.time.Instant;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

/** 身体命令も既存seen・結果再送・ACKを共有し、新しい操作配送経路を作らない。 */
class BodyInspectDeliveryTest {
    @Test void commandReplayAndResultAckReuseExistingState() {
        var state=new DogidoCommandProtocol.State();
        UUID id=UUID.randomUUID();
        assertTrue(state.markCommandSeen(id.toString())); assertFalse(state.markCommandSeen(id.toString()));
        JsonObject result=new JsonObject(); result.addProperty("command_id",id.toString());
        result.addProperty("command_type","inspect_body_target"); result.addProperty("status","succeeded");
        state.rememberResult(result); result.addProperty("status","changed");
        assertEquals("succeeded",state.pendingResultsJson().get(0).getAsJsonObject().get("status").getAsString());
        state.acknowledge(java.util.Set.of(id.toString())); assertEquals(0,state.pendingResultCount());
        assertFalse(state.markCommandSeen(id.toString()));
    }
    @Test void eventEnvelopeKeepsBodyAutonomyIndependentFromBodyObservation() {
        JsonObject autonomy=new JsonObject(); autonomy.addProperty("version",2); autonomy.addProperty("status","unavailable");
        JsonObject observation=new JsonObject(); observation.addProperty("version",3); observation.addProperty("status","unavailable");
        JsonObject event=new EventJsonBuilder("status_snapshot","system","background","high",Instant.now().toString(),
            new dogido.fabric.observation.smell.SmellObserver().current()).bodyObservation(observation).bodyAutonomy(autonomy).finish();
        autonomy.addProperty("version",99); observation.addProperty("version",99);
        assertEquals(2,event.getAsJsonObject("body_autonomy").get("version").getAsInt());
        assertEquals(3,event.getAsJsonObject("body_observation").get("version").getAsInt());
    }
}
