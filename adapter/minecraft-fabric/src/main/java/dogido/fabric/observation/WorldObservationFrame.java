package dogido.fabric.observation;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import java.util.function.Supplier;

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

    public WorldObservationFrame(Supplier<JsonObject> capture, Supplier<JsonObject> captureBlocks,
            Supplier<Materials> captureMaterials) {
        this.capture = capture;
        this.captureBlocks = captureBlocks;
        this.captureMaterials = captureMaterials;
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
}
