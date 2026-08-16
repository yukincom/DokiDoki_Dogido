package dogido.fabric;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;
import java.util.List;
import java.util.Set;

import org.junit.jupiter.api.Test;

import com.google.gson.JsonObject;

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
}
