package dogido.fabric.observation;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.function.Supplier;
import java.util.function.Function;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import net.minecraft.entity.Entity;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;

/**
 * 一回のclient走査周期の元観測を遅延取得し、イベントごとに独立したJSONを返す。
 * インスタンスはrootのtick内だけで使う。戦闘判断や送信は持たず、次tick・別worldへ値を残さない。
 */
public final class WorldObservationFrame {
    /** playerと所持品は同じinventory読取りから構成する。lookTargetと種類表は未取得時null。 */
    public record Materials(JsonObject player, JsonObject inventory, JsonObject nearbyMobs,
            JsonArray droppedItems, JsonArray recentBlockBreaks, JsonObject lookTarget) {
        private Materials copy() {
            return new Materials(player.deepCopy(), inventory.deepCopy(),
                nearbyMobs == null ? null : nearbyMobs.deepCopy(), droppedItems.deepCopy(),
                recentBlockBreaks.deepCopy(), lookTarget == null ? null : lookTarget.deepCopy());
        }
    }

    private final Supplier<JsonObject> capture;
    private JsonObject observation;
    private final Supplier<JsonObject> captureBlocks;
    private JsonObject blocks;
    private final Supplier<Materials> captureMaterials;
    private Materials materials;
    /** 種類と座標は取得時に固定する。entity参照は同じtick内のドラゴンphase等の読取りにだけ使う。 */
    public record EntitySample(String type, Vec3d position, Entity entity) {}
    private record Area(Box bounds, Vec3d center, double radius) {}
    private final Function<Box, List<EntitySample>> captureEntities;
    private final Map<Box, List<EntitySample>> entities = new HashMap<>();
    private final Map<Area, Map<String, Integer>> entityCounts = new HashMap<>();

    public WorldObservationFrame(Supplier<JsonObject> capture, Supplier<JsonObject> captureBlocks,
            Supplier<Materials> captureMaterials, Function<Box, List<EntitySample>> captureEntities) {
        this.capture = capture;
        this.captureBlocks = captureBlocks;
        this.captureMaterials = captureMaterials;
        this.captureEntities = captureEntities;
    }

    public JsonObject blocks() {
        if (blocks == null) blocks = captureBlocks.get();
        return blocks.deepCopy();
    }

    public JsonObject copy() {
        if (observation == null) observation = capture.get();
        return observation.deepCopy();
    }

    /** 送信するイベントがある時だけ一回取得する。コピー後の変更は他イベントへ混ざらない。 */
    public Materials materials() {
        if (materials == null) materials = captureMaterials.get();
        return materials.copy();
    }

    /** 同じ境界boxの検索結果を一回取得する。異なる視点・範囲は別の取得になる。 */
    public List<EntitySample> entities(Box bounds) {
        return entities.computeIfAbsent(bounds, area -> List.copyOf(captureEntities.apply(area)));
    }

    /** center=nullはbox全体、指定時は中心からradiusブロック以内。種類ごとにworldを検索し直さない。 */
    public int countEntities(Box bounds, Vec3d center, double radius, String type) {
        var counts = entityCounts.computeIfAbsent(new Area(bounds, center, radius), area -> {
            Map<String, Integer> result = new HashMap<>();
            for (EntitySample sample : entities(area.bounds())) {
                if (area.center() != null && !(sample.position().distanceTo(area.center()) <= area.radius())) continue;
                result.merge(sample.type(), 1, Integer::sum);
            }
            return result;
        });
        return counts.getOrDefault(type, 0);
    }
}
