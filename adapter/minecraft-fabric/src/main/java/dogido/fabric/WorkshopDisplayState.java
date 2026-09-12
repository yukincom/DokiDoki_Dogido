package dogido.fabric;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/** Closed read-only wire contract and pure presentation timeline. */
final class WorkshopDisplayState {
    record Snapshot(String sessionId, String workshopId, long revision, long observedSequence,
                    String state, List<String> canonical, List<String> pending,
                    boolean editing, Integer selectedLine, boolean provisionalResume) {
        List<String> shownLines() { return pending.isEmpty() ? canonical : pending; }
    }

    static Snapshot parse(String body, String sessionId) {
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
            JsonElement provisional = json.get("provisional_resume");
            if (!provisional.isJsonPrimitive() || !provisional.getAsJsonPrimitive().isBoolean()) return null;
            Long selectedValue = json.get("selected_line").isJsonNull() ? null : integer(json, "selected_line");
            if (selectedValue != null && (selectedValue < 0 || selectedValue > 2)) return null;
            Integer selected = selectedValue == null ? null : selectedValue.intValue();
            long revision = integer(json, "revision");
            long observed = integer(json, "observed_sequence");
            if (revision < 0 || observed < 0) return null;
            return new Snapshot(sessionId, id, revision, observed, state, canonical, pending,
                editingValue.getAsBoolean(), selected, provisional.getAsBoolean());
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

    private Snapshot latest;
    private Snapshot displayed;
    private long lastReceivedMs;
    private long barrierSequence;
    private boolean localDanger;
    private float from;
    private float target;
    private long transitionMs;

    void reset() {
        latest = null; displayed = null; lastReceivedMs = 0; barrierSequence = 0;
        localDanger = false; from = 0; target = 0; transitionMs = 0;
    }

    void synchronizeAfter(long sequence) { barrierSequence = Math.max(barrierSequence, sequence); hideNow(); }

    /** Returns a single edit-cue change, not a sound on each poll or reconnect. */
    boolean receive(Snapshot snapshot, long now) {
        if (snapshot == null) { unavailable(); return false; }
        if (latest != null && snapshot.sessionId().equals(latest.sessionId())
                && snapshot.revision() < latest.revision()) return false;
        boolean cue = latest != null && Objects.equals(latest.workshopId(), snapshot.workshopId())
            && snapshot.editing() && (!latest.editing() || !Objects.equals(latest.selectedLine(), snapshot.selectedLine()));
        latest = snapshot;
        lastReceivedMs = now;
        update(now);
        if (cue && target == 1) { from = 1; transitionMs = now; }
        return cue && target == 1;
    }

    void danger(boolean danger, boolean changed, long sequence, long now) {
        boolean entering = danger && !localDanger;
        localDanger = danger;
        if (danger && (entering || changed)) {
            // A new observation invalidates any older server permission to resume.
            barrierSequence = Math.max(barrierSequence, sequence); hideNow();
        }
        update(now);
    }

    void unavailable() { latest = null; hideNow(); }

    void update(long now) {
        if (latest == null || now - lastReceivedMs > 3000 || (localDanger && !latest.provisionalResume())
                || latest.observedSequence() < barrierSequence || latest.state().equals("danger")) {
            hideNow();
            return;
        }
        boolean show = latest.state().equals("open");
        boolean newWorkshop = show && displayed != null
            && !Objects.equals(latest.workshopId(), displayed.workshopId());
        if (newWorkshop) hideNow();
        if (show) displayed = latest;
        float next = show ? 1 : 0;
        if (target != next) {
            from = opacity(now); target = next; transitionMs = now;
        }
    }

    float opacity(long now) {
        float progress = Math.clamp((now - transitionMs) / 1000f, 0, 1);
        float ease = target == 1 ? progress * progress * (3 - 2 * progress) : 1 - (1 - progress) * (1 - progress);
        return from + (target - from) * ease;
    }

    Snapshot displayed() { return displayed; }
    boolean showing() { return target == 1; }
    private void hideNow() { from = 0; target = 0; }
}
