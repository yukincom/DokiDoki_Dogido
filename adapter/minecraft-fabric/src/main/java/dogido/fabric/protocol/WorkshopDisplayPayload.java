package dogido.fabric.protocol;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.util.ArrayList;
import java.util.List;

public final class WorkshopDisplayPayload {
    private WorkshopDisplayPayload() {}

    public record Snapshot(String sessionId, String workshopId, long revision, long observedSequence,
                    String state, List<String> canonical, List<String> pending,
                    boolean editing, Integer selectedLine, String characterState) {
        public List<String> shownLines() { return pending.isEmpty() ? canonical : pending; }
    }

    public static Snapshot parse(String body, String sessionId) {
        try {
            if (body.length() > 16_384) return null;
            JsonObject json = JsonParser.parseString(body).getAsJsonObject();
            if (integer(json, "schema_version") != 1 || !sessionId.equals(string(json, "session_id"))) return null;
            String state = string(json, "state");
            if (!List.of("closed", "open", "danger").contains(state)) return null;
            String id = json.get("workshop_id").isJsonNull() ? null : string(json, "workshop_id");
            List<String> canonical = lines(json.get("canonical_lines"));
            List<String> pending = lines(json.get("pending_lines"));
            if (!state.equals("closed") && (id == null || id.isBlank() || canonical.size() != 3)) return null;
            JsonElement editingValue = json.get("editing");
            if (!editingValue.isJsonPrimitive() || !editingValue.getAsJsonPrimitive().isBoolean()) return null;
            Long selectedValue = json.get("selected_line").isJsonNull() ? null : integer(json, "selected_line");
            if (selectedValue != null && (selectedValue < 0 || selectedValue > 2)) return null;
            Integer selected = selectedValue == null ? null : selectedValue.intValue();
            long revision = integer(json, "revision");
            long observed = integer(json, "observed_sequence");
            if (revision < 0 || observed < 0) return null;
            // Additive v1 field: older servers keep the normal, blinking character.
            String character = json.has("character_state") ? string(json, "character_state") : "normal";
            if (!List.of("normal", "thinking").contains(character)) return null;
            return new Snapshot(sessionId, id, revision, observed, state, canonical, pending,
                editingValue.getAsBoolean(), selected, character);
        } catch (RuntimeException error) {
            return null;
        }
    }

    private static String string(JsonObject json, String key) {
        JsonElement value = json.get(key);
        if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) throw new IllegalArgumentException(key);
        return value.getAsString();
    }

    private static long integer(JsonObject json, String key) {
        JsonElement value = json.get(key);
        if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isNumber()) throw new IllegalArgumentException(key);
        return value.getAsBigDecimal().longValueExact();
    }

    private static List<String> lines(JsonElement value) {
        var array = value.getAsJsonArray();
        if (array.size() != 0 && array.size() != 3) throw new IllegalArgumentException("line count");
        List<String> result = new ArrayList<>();
        for (JsonElement item : array) {
            if (!item.isJsonPrimitive() || !item.getAsJsonPrimitive().isString()) throw new IllegalArgumentException("line type");
            String line = item.getAsString();
            if (line.isBlank() || line.codePointCount(0, line.length()) > 32
                    || line.codePoints().anyMatch(c -> Character.isISOControl(c) || c == 0xA7)) {
                throw new IllegalArgumentException("line text");
            }
            result.add(line);
        }
        return List.copyOf(result);
    }
}
