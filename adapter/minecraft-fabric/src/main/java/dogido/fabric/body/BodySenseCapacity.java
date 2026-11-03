package dogido.fabric.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonPrimitive;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

/** 身体感覚v1の送信上限。読めた値だけを残し、省略した系列を空の完全観測にしない。 */
final class BodySenseCapacity {
    static final int MAX_BYTES = 65_536;
    private static final List<String> SECTIONS = List.of("visual_threats","passive_mobs","world",
        "hearing","nearby_composition","smell_observation");
    private BodySenseCapacity() {}

    static boolean isIncomplete(JsonObject senses, String section) {
        return senses.has("incomplete") && senses.getAsJsonArray("incomplete").contains(new JsonPrimitive(section));
    }

    static void incomplete(JsonObject senses, String section) {
        JsonArray fields = senses.getAsJsonArray("incomplete");
        if (fields == null) { fields = new JsonArray(); senses.add("incomplete",fields); }
        JsonPrimitive name = new JsonPrimitive(section);
        if (!fields.contains(name)) fields.add(name);
    }

    /** 通常サイズなら手を加えない。超過時だけ、視認個体、天候・場所、実音の順で枠へ収める。 */
    static void fit(JsonObject senses) {
        if (bytes(senses) <= MAX_BYTES) return;
        Set<String> partial = new LinkedHashSet<>();
        if (senses.has("incomplete")) senses.getAsJsonArray("incomplete").forEach(v -> partial.add(v.getAsString()));
        // 最終的に必要になるかもしれない全標識のバイトを先に予約する。
        SECTIONS.forEach(section -> incomplete(senses,section));
        JsonObject world = senses.remove("world").getAsJsonObject();
        JsonObject retainedWorld = new JsonObject();
        senses.add("world",retainedWorld);
        JsonElement smell = senses.remove("smell_observation");
        JsonArray hostile = emptyList(senses,"visual_threats"), passive = emptyList(senses,"passive_mobs");
        JsonArray heardThreats = emptyList(senses,"auditory_threats"), ambient = emptyList(senses,"ambient_sounds");
        JsonObject composition = senses.getAsJsonObject("nearby_composition");
        JsonArray kinds = emptyList(composition,"mobs"), blocks = emptyList(composition,"blocks");
        // 完備のfalseからtrueに変更してもサイズは増えない。
        Space space = new Space(MAX_BYTES - bytes(senses));
        for (String key : List.of("weather","biome","time_of_day","time_phase")) {
            JsonElement value = world.remove(key);
            if (value != null && !space.add(retainedWorld,key,value)) partial.add("world");
        }
        for (int i = 0; i < Math.max(hostile.size(),passive.size()); i++) {
            // どちらも身体から近い順。片方の大群で、もう片方の最寄り個体を押し出さない。
            if (i < hostile.size() && !space.add(senses.getAsJsonArray("visual_threats"),hostile.get(i))) partial.add("visual_threats");
            if (i < passive.size() && !space.add(senses.getAsJsonArray("passive_mobs"),passive.get(i))) partial.add("passive_mobs");
        }
        boolean heardComplete = space.copySounds(heardThreats,senses.getAsJsonArray("auditory_threats"));
        boolean ambientComplete = space.copySounds(ambient,senses.getAsJsonArray("ambient_sounds"));
        if (!heardComplete || !ambientComplete) {
            senses.addProperty("hearing_truncated",true);
            partial.add("hearing");
        }
        if (!space.copy(kinds,composition.getAsJsonArray("mobs"))) {
            composition.addProperty("mobs_truncated",true);
            partial.add("nearby_composition");
        }
        world.entrySet().forEach(field -> {
            if (!space.add(retainedWorld,field.getKey(),field.getValue())) partial.add("world");
        });
        if (!space.copy(blocks,composition.getAsJsonArray("blocks"))) {
            composition.addProperty("blocks_truncated",true);
            partial.add("nearby_composition");
        }
        if (smell != null && !space.add(senses,"smell_observation",smell)) partial.add("smell_observation");
        senses.remove("incomplete");
        partial.forEach(field -> incomplete(senses,field));
    }

    private static JsonArray emptyList(JsonObject object, String key) {
        JsonArray rows = object.getAsJsonArray(key);
        object.add(key,new JsonArray());
        return rows;
    }
    private static int bytes(JsonElement element) {
        return element.toString().getBytes(StandardCharsets.UTF_8).length;
    }

    /** JSONの要素・プロパティを分割しない。長い一件が入らなくても、残りの小さい観測は試す。 */
    private static final class Space {
        private int remaining;
        Space(int remaining) { this.remaining = remaining; }
        boolean add(JsonArray array, JsonElement value) {
            int needed = bytes(value) + (array.isEmpty() ? 0 : 1);
            if (needed > remaining) return false;
            array.add(value); remaining -= needed; return true;
        }
        boolean add(JsonObject object, String key, JsonElement value) {
            int needed = bytes(new JsonPrimitive(key)) + 1 + bytes(value) + (object.size() == 0 ? 0 : 1);
            if (needed > remaining) return false;
            object.add(key,value); remaining -= needed; return true;
        }
        boolean copySounds(JsonArray source, JsonArray target) {
            boolean complete = true;
            for (JsonElement value : source) {
                if (add(target,value)) continue;
                complete = false;
                // 周辺発音候補の根拠だけが大きい場合、実音の種類・方向・年齢は残せる。
                if (value.getAsJsonObject().has("source_context")) {
                    JsonObject sound = value.getAsJsonObject().deepCopy();
                    sound.remove("source_context");
                    add(target,sound);
                }
            }
            return complete;
        }
        boolean copy(JsonArray source, JsonArray target) {
            boolean complete = true;
            for (JsonElement value : source) if (!add(target,value)) complete = false;
            return complete;
        }
    }
}
