package dogido.fabric.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonNull;
import com.google.gson.JsonObject;
import java.time.Instant;
import java.util.Locale;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodyRequestProtocolTest {
    private final BodyInspectProtocol.Context context=new BodyInspectProtocol.Context(UUID.randomUUID(),UUID.randomUUID(),UUID.randomUUID(),"minecraft:overworld");
    private final Instant now=Instant.parse("2026-10-23T02:00:00Z");
    private JsonObject row(BodyRequestProtocol.Mode mode) {
        JsonObject row=new JsonObject(); row.addProperty("type","body_request"); row.addProperty("command_id",UUID.randomUUID().toString());
        row.addProperty("issued_at",now.toString()); row.addProperty("expires_at",now.plusSeconds(5).toString());
        row.addProperty("input_generation",42); row.addProperty("owner_id",context.ownerId().toString()); row.addProperty("dimension",context.dimension());
        row.addProperty("mode",mode.name().toLowerCase(Locale.ROOT)); row.add("context",mode==BodyRequestProtocol.Mode.RECALL?JsonNull.INSTANCE:context.json());
        return row;
    }
    private String envelope(JsonObject row) { JsonObject root=new JsonObject(); JsonArray commands=new JsonArray(); commands.add(row); root.add("commands",commands); return root.toString(); }
    @Test void everyBertBodyModeHasOneWireValue() {
        assertEquals(12,BodyRequestProtocol.Mode.values().length);
        for (var mode:BodyRequestProtocol.Mode.values()) {
            var commands=BodyRequestProtocol.parseCommands(envelope(row(mode)));
            assertEquals(1,commands.size()); assertEquals(mode,commands.getFirst().mode());
            assertEquals(42,commands.getFirst().inputGeneration());
            if (mode==BodyRequestProtocol.Mode.RECALL) assertNull(commands.getFirst().context());
            else assertEquals(context,commands.getFirst().context());
        }
    }
    @Test void lookRequiresMatchingBodyOwnerAndDimension() {
        var value=row(BodyRequestProtocol.Mode.LOOK_PLAYER); value.add("context",JsonNull.INSTANCE);
        assertTrue(BodyRequestProtocol.parseCommands(envelope(value)).isEmpty());
        value=row(BodyRequestProtocol.Mode.LOOK_PLAYER); value.addProperty("owner_id",UUID.randomUUID().toString());
        assertTrue(BodyRequestProtocol.parseCommands(envelope(value)).isEmpty());
        value=row(BodyRequestProtocol.Mode.LOOK_PLAYER); value.addProperty("dimension","minecraft:the_nether");
        assertTrue(BodyRequestProtocol.parseCommands(envelope(value)).isEmpty());
    }
    @Test void oldNamesMissingGenerationAndNonIntegerGenerationsNeverParse() {
        var value=row(BodyRequestProtocol.Mode.RECALL); value.addProperty("mode","call");
        assertTrue(BodyRequestProtocol.parseCommands(envelope(value)).isEmpty());
        value=row(BodyRequestProtocol.Mode.RECALL); value.remove("input_generation");
        assertTrue(BodyRequestProtocol.parseCommands(envelope(value)).isEmpty());
        for (String invalid:new String[]{"-1","1.5","1e2","9999999999999999999"}) {
            value=row(BodyRequestProtocol.Mode.RECALL); value.add("input_generation",com.google.gson.JsonParser.parseString(invalid));
            assertTrue(BodyRequestProtocol.parseCommands(envelope(value)).isEmpty());
        }
    }
    @Test void lifetimeAndActualClockAreCheckedSeparately() {
        var value=row(BodyRequestProtocol.Mode.LOOK_TARGET);
        var command=BodyRequestProtocol.parseCommands(envelope(value)).getFirst();
        assertNull(command.timeError(now)); assertEquals("expired",command.timeError(now.plusSeconds(5)));
        assertEquals("issued_in_future",command.timeError(now.minusSeconds(1)));
        value.addProperty("expires_at",now.plusSeconds(6).toString()); assertTrue(BodyRequestProtocol.parseCommands(envelope(value)).isEmpty());
    }
    @Test void recallResultCarriesTheActuallyResolvedEpochAndOriginalGeneration() {
        var command=BodyRequestProtocol.parseCommands(envelope(row(BodyRequestProtocol.Mode.RECALL))).getFirst();
        var result=BodyRequestProtocol.result(command,"succeeded","arrived",now,context);
        assertEquals("body_request",result.get("command_type").getAsString());
        assertEquals(context.epoch().toString(),result.get("epoch").getAsString());
        assertEquals(context.ownerId().toString(),result.get("owner_id").getAsString());
        assertEquals(42,result.get("input_generation").getAsLong());
        var rejected=BodyRequestProtocol.result(command,"rejected","body_chunk_not_loaded",now,null);
        assertFalse(rejected.has("body_id")); assertFalse(rejected.has("epoch"));
    }
}
