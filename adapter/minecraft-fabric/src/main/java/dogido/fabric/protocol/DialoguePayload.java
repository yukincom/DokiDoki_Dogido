package dogido.fabric.protocol;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.util.ArrayList;
import java.util.List;

/** The existing dialogue snapshot, projected onto the fields the local chat can display. */
public final class DialoguePayload {
    private DialoguePayload() {}
    public static final int MAX_INPUT_CODE_POINTS = 1000;
    private static final int MAX_RESPONSE_BYTES = 4 * 1024 * 1024;

    public record Scope(String sessionId, String instance) {}
    public record Row(String turnId, String source, String playerInput, String displayId, String text,
                      String playbackStatus) {}
    public record Snapshot(Scope scope, boolean voiceEnabled, String voiceGeneration, boolean textEnabled,
                           List<Row> rows) {}
    public record Reply(boolean accepted, String reason, Boolean voiceEnabled, String voiceGeneration) {
        public static Reply failure(String reason) { return new Reply(false, reason, null, ""); }
    }
    public record Acknowledgement(boolean accepted, List<String> displayed, List<String> rejected) {
        public boolean covers(List<String> ids) {
            return accepted && ids.stream().allMatch(id -> displayed.contains(id) || rejected.contains(id));
        }
        public static Acknowledgement failed() { return new Acknowledgement(false, List.of(), List.of()); }
    }

    public static String inputError(String text) {
        if (text == null || text.isBlank()) return "empty_text";
        if (text.codePointCount(0, text.length()) > MAX_INPUT_CODE_POINTS) return "text_too_long";
        return null;
    }

    public static Snapshot snapshot(String body, String sessionId) {
        try {
            JsonObject json = object(body);
            JsonObject session = null;
            for (JsonElement candidate : json.getAsJsonArray("sessions")) {
                JsonObject item = candidate.getAsJsonObject();
                if (sessionId.equals(optionalString(item, "session_id"))) {
                    if (session != null) return null;
                    session = item;
                }
            }
            if (session == null) return null;
            Scope scope = new Scope(sessionId, identifier(session, "session_instance"));
            List<Row> rows = new ArrayList<>();
            boolean separateTranscriptions = session.has("voice_inputs");
            if (separateTranscriptions) {
                for (JsonElement element : session.getAsJsonArray("voice_inputs")) {
                    JsonObject input = element.getAsJsonObject();
                    rows.add(new Row(identifier(input, "input_id"), "voice", optionalString(input, "text"), "", "", "recognized"));
                }
            }
            for (JsonElement element : json.getAsJsonArray("utterances")) {
                JsonObject row = element.getAsJsonObject();
                if (!sessionId.equals(optionalString(row, "session_id"))) continue;
                String instance = optionalString(row, "session_instance");
                if (!instance.isEmpty() && !instance.equals(scope.instance())) continue;
                rows.add(new Row(optionalString(row, "turn_id"), separateTranscriptions ? "" : optionalString(row, "source"),
                    optionalString(row, "player_input_text"), optionalString(row, "display_id"),
                    optionalString(row, "text"), optionalString(row, "playback_status")));
            }
            return new Snapshot(scope, bool(session, "voice_enabled"), identifier(session, "voice_generation"),
                bool(session, "text_enabled"), List.copyOf(rows));
        } catch (RuntimeException invalid) {
            return null;
        }
    }

    public static Reply reply(String body) {
        try {
            JsonObject json = object(body);
            boolean accepted = bool(json, "accepted");
            Boolean voice = json.has("voice_enabled") ? bool(json, "voice_enabled") : null;
            String generation = optionalString(json, "voice_generation");
            return new Reply(accepted, optionalString(json, "reason"), voice, generation);
        } catch (RuntimeException invalid) {
            return Reply.failure("invalid_response");
        }
    }

    public static Acknowledgement acknowledgement(String body) {
        try {
            JsonObject json = object(body);
            return new Acknowledgement(bool(json, "accepted"), strings(json, "displayed"), strings(json, "rejected"));
        } catch (RuntimeException invalid) {
            return Acknowledgement.failed();
        }
    }

    private static JsonObject object(String body) {
        if (body == null || body.length() > MAX_RESPONSE_BYTES) throw new IllegalArgumentException("body size");
        return JsonParser.parseString(body).getAsJsonObject();
    }

    private static String optionalString(JsonObject json, String key) {
        JsonElement value = json.get(key);
        if (value == null || value.isJsonNull()) return "";
        if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) throw new IllegalArgumentException(key);
        return value.getAsString();
    }

    private static String identifier(JsonObject json, String key) {
        String value = optionalString(json, key);
        if (value.isBlank() || value.length() > 256) throw new IllegalArgumentException(key);
        return value;
    }

    private static boolean bool(JsonObject json, String key) {
        JsonElement value = json.get(key);
        if (value == null || !value.isJsonPrimitive() || !value.getAsJsonPrimitive().isBoolean()) {
            throw new IllegalArgumentException(key);
        }
        return value.getAsBoolean();
    }

    private static List<String> strings(JsonObject json, String key) {
        List<String> result = new ArrayList<>();
        for (JsonElement value : json.getAsJsonArray(key)) {
            if (!value.isJsonPrimitive() || !value.getAsJsonPrimitive().isString()) throw new IllegalArgumentException(key);
            result.add(value.getAsString());
        }
        return List.copyOf(result);
    }
}
