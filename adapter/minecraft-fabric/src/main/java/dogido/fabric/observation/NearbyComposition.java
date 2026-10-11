package dogido.fabric.observation;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.Collection;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;

/** 種類を比較する送信表。友好・中立Mobの数量は会話の参考にだけ使う。 */
public final class NearbyComposition {
    static final int LIMIT = 128;
    public static final double MOB_ENTER = 16.0;
    public static final double MOB_EXIT = 21.0;
    public static final int BLOCK_MARGIN = 5;

    /** 新規の種類だけ内側で発見し、既知の種類は外側の実視認まで保つ。未視認を補完しない。 */
    public static final class RangeTracker {
        private final double enter;
        private final double exit;
        private Set<String> previous = Set.of();
        public RangeTracker(double enter, double exit) {
            this.enter = enter;
            this.exit = exit;
        }
        public boolean allows(String id, double distance) {
            return Double.isFinite(distance) && distance >= 0
                && distance <= (previous.contains(id) ? exit : enter);
        }
        public JsonObject finish(Collection<String> actuallyVisible) {
            previous = Set.copyOf(actuallyVisible);
            return types(previous);
        }
        public void reset() { previous = Set.of(); }
    }
    /** 既存の水平±8・下3・上4ブロックの箱から、何ブロック外側か。 */
    public static double blockPadding(int dx, int dy, int dz) {
        return Math.max(0, Math.max(Math.max(Math.abs(dx) - 8, Math.abs(dz) - 8),
            dy < 0 ? -dy - 3 : dy - 4));
    }

    /** 呼出元が視認・範囲・非敵対を確認した全個体。4個体の提示上限より前に集計する。 */
    public record Mob(String type, boolean baby, String profession) {}

    public static JsonObject mobs(Collection<Mob> input) {
        Map<String, Integer> totals = new TreeMap<>();
        Map<String, Map<String, Integer>> professions = new TreeMap<>();
        for (Mob mob : input) {
            totals.merge(mob.type(), 1, Integer::sum);
            if (mob.type().equals("villager") || mob.type().equals("minecraft:villager")) {
                String profession = mob.baby() ? "baby"
                    : mob.profession() == null || mob.profession().isBlank() ? "unknown" : mob.profession();
                professions.computeIfAbsent(mob.type(), unused -> new TreeMap<>())
                    .merge(profession, 1, Integer::sum);
            }
        }
        JsonArray entries = new JsonArray();
        boolean truncated = totals.size() > LIMIT;
        for (var total : totals.entrySet().stream().limit(LIMIT).toList()) {
            JsonObject row = quantity(total.getKey(), total.getValue());
            Map<String, Integer> groups = professions.getOrDefault(total.getKey(), Map.of());
            JsonArray breakdown = new JsonArray();
            groups.entrySet().stream().limit(LIMIT).forEach(group ->
                breakdown.add(quantity(group.getKey(), group.getValue())));
            row.add("professions", breakdown);
            entries.add(row);
            truncated |= groups.size() > LIMIT;
        }
        JsonObject result = new JsonObject();
        result.add("entries", entries);
        result.addProperty("truncated", truncated);
        return result;
    }

    private static JsonObject quantity(String id, int count) {
        JsonObject result = new JsonObject();
        result.addProperty("id", id);
        if (count >= 5) result.addProperty("quantity", "many");
        else result.addProperty("quantity", count);
        return result;
    }

    public static JsonObject types(Collection<String> input) {
        TreeSet<String> sorted = new TreeSet<>(input);
        JsonArray ids = new JsonArray();
        sorted.stream().limit(LIMIT).forEach(ids::add);
        JsonObject result = new JsonObject();
        result.add("ids", ids);
        result.addProperty("truncated", sorted.size() > LIMIT);
        return result;
    }
}
