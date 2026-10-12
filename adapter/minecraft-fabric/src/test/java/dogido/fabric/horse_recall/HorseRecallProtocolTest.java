package dogido.fabric.horse_recall;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.stream.IntStream;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class HorseRecallProtocolTest {
    private static final UUID WORLD = UUID.fromString("00000000-0000-0000-0000-000000000001");
    private static final UUID PLAYER = UUID.fromString("00000000-0000-0000-0000-000000000002");
    private static final UUID HORSE = UUID.fromString("00000000-0000-0000-0000-000000000003");
    private static final Instant NOW = Instant.parse("2026-10-11T01:00:00Z");

    private static String command() {
        return """
            {"commands":[{"type":"recall_horse","command_id":"cmd-horse",
            "world_id":"00000000-0000-0000-0000-000000000001","player_uuid":"00000000-0000-0000-0000-000000000002",
            "dimension":"minecraft:overworld","entity_id":"00000000-0000-0000-0000-000000000003",
            "issued_at":"2026-10-11T01:00:00Z","expires_at":"2026-10-11T01:00:05Z"}]}
            """;
    }

    @Test void closedCommandUsesUuidAndFiveSecondLifetime() {
        var parsed = HorseRecallProtocol.parseCommands(command());
        assertEquals(1, parsed.size());
        assertEquals(HORSE, parsed.getFirst().entityId());
        assertEquals(WORLD, parsed.getFirst().worldId());
        assertNull(parsed.getFirst().timeError(NOW.plusSeconds(4)));
        assertEquals("expired", parsed.getFirst().timeError(NOW.plusSeconds(5)));
        assertEquals("issued_in_future", parsed.getFirst().timeError(NOW.minusSeconds(1)));
    }

    @Test void malformedTypesLongLifetimesAndCommandsCannotReachExecutor() {
        for (String invalid : List.of(command().replace("recall_horse", "run_command"),
                command().replace("01:00:05", "01:00:06"), command().replace("01:00:05", "01:00:00"),
                command().replace("minecraft:overworld", "/tp @e ~ ~ ~"),
                command().replace("00000000-0000-0000-0000-000000000003", "0-0-0-0-3"),
                command().replace("\"cmd-horse\"", "123"), "{}", "null", "not-json")) {
            assertTrue(HorseRecallProtocol.parseCommands(invalid).isEmpty(), invalid);
        }
        JsonObject root = JsonParser.parseString(command()).getAsJsonObject();
        for (String required : List.of("command_id", "world_id", "player_uuid", "entity_id", "dimension", "issued_at", "expires_at")) {
            JsonObject copy = root.deepCopy();
            copy.getAsJsonArray("commands").get(0).getAsJsonObject().remove(required);
            assertTrue(HorseRecallProtocol.parseCommands(copy.toString()).isEmpty(), required);
        }
    }

    @Test void unnamedAndDuplicateNamedOwnedHorsesRetainTheirIndividualUuids() {
        var one = new HorseRecallProtocol.Candidate(HORSE, "horse", null, true, PLAYER, true, true, true, "minecraft:overworld", false, false, false);
        var two = new HorseRecallProtocol.Candidate(WORLD, "pig", "ぶち", false, null, true, true, true, "minecraft:overworld", false, false, false);
        var three = new HorseRecallProtocol.Candidate(PLAYER, "horse", "ぶち", true, PLAYER, true, true, true, "minecraft:overworld", false, false, false);
        JsonObject json = new HorseRecallProtocol.Snapshot(true, WORLD, 1, NOW, PLAYER, "minecraft:overworld", HORSE,
            List.of(one, two, three)).toJson();
        assertEquals("available", json.get("status").getAsString());
        assertEquals(2, json.get("schema_version").getAsInt());
        var pig = json.getAsJsonArray("candidates").get(1).getAsJsonObject();
        assertEquals("pig", pig.get("entity_type").getAsString());
        assertTrue(pig.get("owner_uuid").isJsonNull());
        assertFalse(pig.get("tamed").getAsBoolean());
        assertTrue(pig.get("equipped").getAsBoolean());
        assertTrue(json.getAsJsonArray("candidates").get(0).getAsJsonObject().get("custom_name").isJsonNull());
        assertEquals(3, json.getAsJsonArray("candidates").size());
        assertEquals(HORSE.toString(), json.get("recent_mount_id").getAsString());
    }

    @Test void oversizedOrUnavailableSnapshotNeverPublishesAPartialList() {
        var rows = IntStream.range(0, 33).mapToObj(i -> new HorseRecallProtocol.Candidate(new UUID(0, i),
            "horse", "馬" + i, true, PLAYER, true, true, true, "minecraft:overworld", false, false, false)).toList();
        for (var snapshot : List.of(new HorseRecallProtocol.Snapshot(true, WORLD, 2, NOW, PLAYER, "minecraft:overworld", null, rows),
                new HorseRecallProtocol.Snapshot(false, WORLD, 2, NOW, PLAYER, "minecraft:overworld", null, rows.subList(0, 1)))) {
            assertEquals("unavailable", snapshot.toJson().get("status").getAsString());
            assertEquals(0, snapshot.toJson().getAsJsonArray("candidates").size());
        }
        assertEquals(32, new HorseRecallProtocol.Snapshot(true, WORLD, 2, NOW, PLAYER, "minecraft:overworld", null,
            rows.subList(0, 32)).candidates().size());
    }

    @Test void resultReportsMovementWithoutClaimingPresentationCompletion() {
        var result = HorseRecallProtocol.result(HorseRecallProtocol.parseCommands(command()).getFirst(), "succeeded", "recalled", NOW);
        assertEquals("recall_horse", result.get("command_type").getAsString());
        assertEquals(HORSE.toString(), result.get("entity_id").getAsString());
        assertEquals(WORLD.toString(), result.get("world_id").getAsString());
        assertFalse(result.has("extra"));
        assertFalse(result.has("presentation_completed"));
    }
}
