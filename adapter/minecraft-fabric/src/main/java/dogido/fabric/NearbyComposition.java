package dogido.fabric;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.Collection;
import java.util.TreeSet;
import java.util.Set;

/** 個体・座標・個数ではなく、今回確認した種類を比較するための送信表。 */
final class NearbyComposition {
    static final int LIMIT = 128;
    static final double MOB_ENTER = 16.0;
    static final double MOB_EXIT = 21.0;
    static final int BLOCK_MARGIN = 5;

    /** 新規の種類だけ内側で発見し、既知の種類は外側の実視認まで保つ。未視認を補完しない。 */
    static final class RangeTracker {
        private final double enter;
        private final double exit;
        private Set<String> previous = Set.of();
        RangeTracker(double enter, double exit) {
            this.enter = enter;
            this.exit = exit;
        }
        boolean allows(String id, double distance) {
            return Double.isFinite(distance) && distance >= 0
                && distance <= (previous.contains(id) ? exit : enter);
        }
        JsonObject finish(Collection<String> actuallyVisible) {
            previous = Set.copyOf(actuallyVisible);
            return types(previous);
        }
        void reset() { previous = Set.of(); }
    }
    /** 既存の水平±8・下3・上4ブロックの箱から、何ブロック外側か。 */
    static double blockPadding(int dx, int dy, int dz) {
        return Math.max(0, Math.max(Math.max(Math.abs(dx) - 8, Math.abs(dz) - 8),
            dy < 0 ? -dy - 3 : dy - 4));
    }

    static JsonObject types(Collection<String> input) {
        TreeSet<String> sorted = new TreeSet<>(input);
        JsonArray ids = new JsonArray();
        sorted.stream().limit(LIMIT).forEach(ids::add);
        JsonObject result = new JsonObject();
        result.add("ids", ids);
        result.addProperty("truncated", sorted.size() > LIMIT);
        return result;
    }
}
