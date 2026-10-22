package dogido.fabric.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static dogido.fabric.body.BodyInspectProtocol.*;

/** 外形、寿命、座標と候補IDの一致をwire境界で検査する。 */
class BodyInspectProtocolTest {
    static JsonObject wire() {
        var command=BodyInspectControllerTest.command(); JsonObject row=new JsonObject();
        row.addProperty("type","inspect_body_target"); row.addProperty("purpose","inspect"); row.addProperty("command_id",command.commandId().toString());
        row.addProperty("issued_at",command.issuedAt().toString()); row.addProperty("expires_at",command.expiresAt().toString());
        row.add("context",command.context().json()); row.add("target",command.target().json()); return row;
    }
    static String envelope(JsonObject row) { JsonArray commands=new JsonArray(); commands.add(row); JsonObject root=new JsonObject(); root.add("commands",commands); return root.toString(); }
    @Test void entityAndBlockRoundTripAndInvalidRowsDoNotDiscardValidNeighbor() {
        JsonObject row=wire(); assertEquals(1,parseCommands(envelope(row)).size());
        Point point=new Point(-2.5,64.5,3.5);
        Target block=new Target(blockId(point,"minecraft:stone"),"block","minecraft:stone","Stone",point,null);
        row.add("target",block.json()); assertEquals(block,parseCommands(envelope(row)).getFirst().target());
        JsonArray commands=new JsonArray(); commands.add("bad"); commands.add(row); JsonObject root=new JsonObject(); root.add("commands",commands);
        assertEquals(1,parseCommands(root.toString()).size());
        row.getAsJsonObject("target").getAsJsonObject("position").addProperty("x",-2.49);
        assertTrue(parseCommands(envelope(row)).isEmpty());
    }
    @Test void invalidIdentityCoordinateContextAndLifetimeAreRejected() {
        for (String invalid : java.util.List.of("id","entity","coordinate","dimension","expiry","type","uuid")) {
            JsonObject row=wire(); JsonObject target=row.getAsJsonObject("target");
            switch (invalid) {
                case "id" -> target.addProperty("id","e:else");
                case "entity" -> target.addProperty("entity_id","bad");
                case "coordinate" -> target.getAsJsonObject("position").addProperty("x","8");
                case "dimension" -> row.getAsJsonObject("context").addProperty("dimension","some world");
                case "expiry" -> row.addProperty("expires_at",BodyInspectControllerTest.NOW.plusSeconds(6).toString());
                case "type" -> target.addProperty("kind","command");
                case "uuid" -> row.addProperty("command_id","1-1-1-1-1");
            }
            assertTrue(parseCommands(envelope(row)).isEmpty(),invalid);
        }
    }
    @Test void unicodeLabelLimitCountsCharactersAndRejectsControlText() {
        JsonObject row=wire(); row.getAsJsonObject("target").addProperty("label","🐮".repeat(80));
        assertEquals(1,parseCommands(envelope(row)).size());
        row.getAsJsonObject("target").addProperty("label","🐮".repeat(81)); assertTrue(parseCommands(envelope(row)).isEmpty());
        row.getAsJsonObject("target").addProperty("label","Cow\nName"); assertTrue(parseCommands(envelope(row)).isEmpty());
    }
    @Test void rejectsNonfiniteAndOutOfWorldPositions() {
        assertThrows(IllegalArgumentException.class,() -> new Point(Double.NaN,0,0));
        assertThrows(IllegalArgumentException.class,() -> new Point(0,Double.POSITIVE_INFINITY,0));
        assertThrows(IllegalArgumentException.class,() -> new Point(30_000_001,0,0));
    }
    @Test void ownerPurposesAreClosedAndCannotSmuggleATarget() {
        for (String purpose : java.util.List.of("owner_near","owner_face")) {
            var row=wire(); row.addProperty("purpose",purpose);
            assertTrue(parseCommands(envelope(row)).isEmpty());
            row.add("target",com.google.gson.JsonNull.INSTANCE);
            var command=parseCommands(envelope(row)).getFirst();
            assertEquals(purpose,command.purpose().name().toLowerCase(java.util.Locale.ROOT));
            assertNull(command.target());
        }
        var row=wire(); row.addProperty("purpose","teleport"); assertTrue(parseCommands(envelope(row)).isEmpty());
        row.remove("purpose"); assertTrue(parseCommands(envelope(row)).isEmpty());
        row=wire(); row.add("target",com.google.gson.JsonNull.INSTANCE); assertTrue(parseCommands(envelope(row)).isEmpty());
    }
    @Test void imageSelectionAcceptsAppearanceAndRejectsCoordinates() {
        var row=wire();row.add("target",com.google.gson.JsonNull.INSTANCE);
        var image=new JsonObject();image.addProperty("capture_id","view_12345678-1234-1234-1234-123456789abc");
        image.addProperty("description","黄色い花");image.addProperty("kind","flower");image.addProperty("color","yellow");image.addProperty("horizontal","left");image.addProperty("vertical","any");row.add("image_target",image);
        assertEquals("yellow",parseCommands(envelope(row)).getFirst().imageTarget().color());
        image.addProperty("z",99);assertTrue(parseCommands(envelope(row)).isEmpty());image.remove("z");
        image.addProperty("x",.3);assertTrue(parseCommands(envelope(row)).isEmpty());image.remove("x");
        row.addProperty("purpose","owner_face");assertTrue(parseCommands(envelope(row)).isEmpty());
    }

}
