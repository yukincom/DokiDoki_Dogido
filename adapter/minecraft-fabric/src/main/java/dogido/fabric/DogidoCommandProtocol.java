package dogido.fabric;

import java.time.Instant;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

final class DogidoCommandProtocol {
    static final int MAX_REMEMBERED_COMMANDS = 2048;

    private DogidoCommandProtocol() {
    }

    record SelectHotbarCommand(
        String commandId,
        int slot,
        String expectedItemId,
        Instant issuedAt,
        Instant expiresAt
    ) {
    }

    static List<SelectHotbarCommand> parseSelectHotbarCommands(String responseBody) {
        List<SelectHotbarCommand> commands = new ArrayList<>();
        JsonObject root = parseObject(responseBody);
        if (root == null || !root.has("commands") || !root.get("commands").isJsonArray()) {
            return commands;
        }
        for (JsonElement element : root.getAsJsonArray("commands")) {
            if (!element.isJsonObject()) {
                continue;
            }
            JsonObject row = element.getAsJsonObject();
            if (!"select_hotbar".equals(stringValue(row, "type"))) {
                continue;
            }
            String commandId = stringValue(row, "command_id");
            String expectedItemId = stringValue(row, "expected_item_id");
            Integer slot = intValue(row, "slot");
            Instant issuedAt = instantValue(row, "issued_at");
            Instant expiresAt = instantValue(row, "expires_at");
            if (
                commandId == null
                    || commandId.isBlank()
                    || expectedItemId == null
                    || expectedItemId.isBlank()
                    || slot == null
                    || slot < 0
                    || slot > 8
                    || issuedAt == null
                    || expiresAt == null
                    || !expiresAt.isAfter(issuedAt)
            ) {
                continue;
            }
            commands.add(new SelectHotbarCommand(commandId, slot, expectedItemId, issuedAt, expiresAt));
        }
        return commands;
    }

    static Set<String> parseAcknowledgedCommandIds(String responseBody) {
        Set<String> commandIds = new LinkedHashSet<>();
        JsonObject root = parseObject(responseBody);
        if (
            root == null
                || !root.has("acknowledged_command_ids")
                || !root.get("acknowledged_command_ids").isJsonArray()
        ) {
            return commandIds;
        }
        for (JsonElement element : root.getAsJsonArray("acknowledged_command_ids")) {
            if (!element.isJsonPrimitive() || !element.getAsJsonPrimitive().isString()) {
                continue;
            }
            String commandId = element.getAsString().trim();
            if (!commandId.isEmpty()) {
                commandIds.add(commandId);
            }
        }
        return commandIds;
    }

    static boolean isUnknownSessionResponse(int statusCode, String responseBody) {
        if (statusCode != 409) {
            return false;
        }
        JsonObject root = parseObject(responseBody);
        if (root == null || !root.has("detail") || !root.get("detail").isJsonObject()) {
            return false;
        }
        return "unknown_session_id".equals(stringValue(root.getAsJsonObject("detail"), "code"));
    }

    static JsonObject commandResult(
        SelectHotbarCommand command,
        String status,
        Instant executedAt,
        Integer selectedSlot,
        String selectedItemId,
        String detailCode
    ) {
        JsonObject result = new JsonObject();
        result.addProperty("command_id", command.commandId());
        result.addProperty("command_type", "select_hotbar");
        result.addProperty("status", status);
        result.addProperty("executed_at", executedAt.toString());
        if (selectedSlot != null) {
            result.addProperty("selected_slot", selectedSlot);
        }
        if (selectedItemId != null && !selectedItemId.isBlank()) {
            result.addProperty("selected_item_id", selectedItemId);
        }
        result.addProperty("detail_code", detailCode == null ? "" : detailCode);
        return result;
    }

    private static JsonObject parseObject(String text) {
        if (text == null || text.isBlank()) {
            return null;
        }
        try {
            JsonElement parsed = JsonParser.parseString(text);
            return parsed.isJsonObject() ? parsed.getAsJsonObject() : null;
        } catch (RuntimeException error) {
            return null;
        }
    }

    private static String stringValue(JsonObject row, String key) {
        try {
            return row.has(key) && row.get(key).isJsonPrimitive() ? row.get(key).getAsString() : null;
        } catch (RuntimeException error) {
            return null;
        }
    }

    private static Integer intValue(JsonObject row, String key) {
        try {
            return row.has(key) && row.get(key).isJsonPrimitive() ? row.get(key).getAsInt() : null;
        } catch (RuntimeException error) {
            return null;
        }
    }

    private static Instant instantValue(JsonObject row, String key) {
        String value = stringValue(row, key);
        if (value == null) {
            return null;
        }
        try {
            return Instant.parse(value);
        } catch (DateTimeParseException error) {
            return null;
        }
    }

    static final class State {
        private final LinkedHashSet<String> seenCommandIds = new LinkedHashSet<>();
        private final LinkedHashMap<String, JsonObject> pendingResults = new LinkedHashMap<>();

        synchronized boolean markCommandSeen(String commandId) {
            if (this.seenCommandIds.contains(commandId)) {
                return false;
            }
            if (this.seenCommandIds.size() >= MAX_REMEMBERED_COMMANDS) {
                String oldest = this.seenCommandIds.iterator().next();
                this.seenCommandIds.remove(oldest);
            }
            this.seenCommandIds.add(commandId);
            return true;
        }

        synchronized void rememberResult(JsonObject result) {
            String commandId = stringValue(result, "command_id");
            if (commandId == null || commandId.isBlank()) {
                return;
            }
            this.pendingResults.put(commandId, result.deepCopy());
        }

        synchronized JsonArray pendingResultsJson() {
            JsonArray array = new JsonArray();
            for (JsonObject result : this.pendingResults.values()) {
                array.add(result.deepCopy());
            }
            return array;
        }

        synchronized void acknowledge(Set<String> commandIds) {
            for (String commandId : commandIds) {
                this.pendingResults.remove(commandId);
            }
        }

        synchronized int pendingResultCount() {
            return this.pendingResults.size();
        }
    }
}
