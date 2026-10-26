package dogido.fabric.protocol;

import com.google.gson.JsonObject;
import java.time.Instant;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class DogidoCommandProtocolTest {
    @Test
    void parsesOnlyClosedValidSelectHotbarCommands() {
        String body = """
            {
              "commands": [
                {"command_id":"cmd_ok","type":"select_hotbar","slot":2,
                 "expected_item_id":"minecraft:stone_sword",
                 "issued_at":"2026-08-15T12:00:00+09:00","expires_at":"2026-08-15T12:00:02+09:00"},
                {"command_id":"cmd_bad","type":"select_hotbar","slot":9,
                 "expected_item_id":"minecraft:stone_sword",
                 "issued_at":"2026-08-15T12:00:00Z","expires_at":"2026-08-15T12:00:02Z"},
                {"command_id":"cmd_text","type":"run_command","slot":1,
                 "expected_item_id":"minecraft:stone_sword",
                 "issued_at":"2026-08-15T12:00:00Z","expires_at":"2026-08-15T12:00:02Z"}
              ]
            }
            """;

        List<DogidoCommandProtocol.SelectHotbarCommand> parsed =
            DogidoCommandProtocol.parseSelectHotbarCommands(body);

        assertEquals(1, parsed.size());
        assertEquals("cmd_ok", parsed.getFirst().commandId());
        assertEquals(2, parsed.getFirst().slot());
    }

    @Test
    void commandStateDeduplicatesAndKeepsResultsUntilAck() {
        DogidoCommandProtocol.State state = new DogidoCommandProtocol.State();
        assertTrue(state.markCommandSeen("cmd_1"));
        assertFalse(state.markCommandSeen("cmd_1"));

        var command = new DogidoCommandProtocol.SelectHotbarCommand(
            "cmd_1",
            1,
            "minecraft:stone_sword",
            Instant.parse("2026-08-15T12:00:00Z"),
            Instant.parse("2026-08-15T12:00:02Z")
        );
        JsonObject result = DogidoCommandProtocol.commandResult(
            command,
            "succeeded",
            Instant.parse("2026-08-15T12:00:01Z"),
            1,
            "minecraft:stone_sword",
            "selected"
        );
        state.rememberResult(result);

        assertEquals(1, state.pendingResultCount());
        assertEquals(1, state.pendingResultsJson().size());
        state.acknowledge(Set.of("cmd_1"));
        assertEquals(0, state.pendingResultCount());
    }

    @Test
    void parsesAcknowledgementsWithoutDuplicates() {
        Set<String> ids = DogidoCommandProtocol.parseAcknowledgedCommandIds(
            "{\"acknowledged_command_ids\":[\"cmd_1\",\"cmd_1\",\"cmd_2\"]}"
        );
        assertEquals(Set.of("cmd_1", "cmd_2"), ids);
    }

    @Test
    void recognizesOnlyTypedUnknownSessionConflict() {
        String unknown = """
            {"detail":{"code":"unknown_session_id","session_id":"ses_old"}}
            """;

        assertTrue(DogidoCommandProtocol.isUnknownSessionResponse(409, unknown));
        assertFalse(DogidoCommandProtocol.isUnknownSessionResponse(404, unknown));
        assertFalse(DogidoCommandProtocol.isUnknownSessionResponse(409, "{\"detail\":\"other\"}"));
        assertFalse(DogidoCommandProtocol.isUnknownSessionResponse(409, "not-json"));
    }

    @Test
    void sharedResponseKeepsMixedCommandsAndAcknowledgementsIndependent() {
        JsonObject root = DogidoCommandProtocol.parseObject("""
            {"acknowledged_command_ids":["old","old",17,null],"commands":[
              null, "invalid", {"type":"select_hotbar","slot":99},
              {"type":"body_request","mode":"invalid"},
              {"type":"select_hotbar","command_id":"hotbar","slot":2,
               "expected_item_id":"minecraft:stone_sword",
               "issued_at":"2026-10-27T00:00:00Z","expires_at":"2026-10-27T00:00:03Z"},
              {"type":"body_request","command_id":"00000000-0000-0000-0000-000000000001",
               "owner_id":"00000000-0000-0000-0000-000000000002","input_generation":7,
               "dimension":"minecraft:overworld","mode":"recall",
               "issued_at":"2026-10-27T00:00:00Z","expires_at":"2026-10-27T00:00:03Z"},
              {"type":"recall_horse","command_id":"horse",
               "world_id":"00000000-0000-0000-0000-000000000001",
               "player_uuid":"00000000-0000-0000-0000-000000000002",
               "entity_id":"00000000-0000-0000-0000-000000000003","dimension":"minecraft:overworld",
               "issued_at":"2026-10-27T00:00:00Z","expires_at":"2026-10-27T00:00:03Z"}]}
            """);
        JsonObject original = root.deepCopy();
        assertEquals(Set.of("old"), DogidoCommandProtocol.parseAcknowledgedCommandIds(root));
        assertEquals("hotbar", DogidoCommandProtocol.parseSelectHotbarCommands(root).getFirst().commandId());
        assertEquals(7, dogido.fabric.body.BodyRequestProtocol.parseCommands(root).getFirst().inputGeneration());
        assertEquals("horse", dogido.fabric.horse_recall.HorseRecallProtocol.parseCommands(root).getFirst().commandId());
        assertTrue(dogido.fabric.body.BodyInspectProtocol.parseCommands(root).isEmpty());
        assertTrue(dogido.fabric.screenshot.ScreenshotProtocol.parseCommands(root).isEmpty());
        assertTrue(dogido.fabric.screenshot.BodyViewProtocol.parseCommands(root).isEmpty());
        assertEquals(original, root);
    }

    @Test
    void invalidResponseIsEmptyForEverySharedConsumer() {
        for (String body : new String[]{null, "", "not-json", "[]", "null", "17", "{\"commands\":17}"}) {
            JsonObject root = DogidoCommandProtocol.parseObject(body);
            assertTrue(DogidoCommandProtocol.parseAcknowledgedCommandIds(root).isEmpty());
            assertTrue(DogidoCommandProtocol.parseSelectHotbarCommands(root).isEmpty());
            assertTrue(dogido.fabric.body.BodyRequestProtocol.parseCommands(root).isEmpty());
            assertTrue(dogido.fabric.body.BodyInspectProtocol.parseCommands(root).isEmpty());
            assertTrue(dogido.fabric.screenshot.ScreenshotProtocol.parseCommands(root).isEmpty());
            assertTrue(dogido.fabric.screenshot.BodyViewProtocol.parseCommands(root).isEmpty());
            assertTrue(dogido.fabric.horse_recall.HorseRecallProtocol.parseCommands(root).isEmpty());
        }
    }
}
