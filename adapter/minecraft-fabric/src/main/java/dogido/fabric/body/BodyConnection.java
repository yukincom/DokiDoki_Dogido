package dogido.fabric.body;

import com.google.gson.JsonObject;
import java.util.List;
import java.util.UUID;
import net.fabricmc.loader.api.FabricLoader;

/** 任意modの有無を先に判定する境界。API未導入でもこの型は読め、client tickへJSONだけを返す。 */
public final class BodyConnection {
    /** APIクラスを参照しない接続内の写し。hearingはHTTPへ直接流さず、身体用collectorが既存音観測へ投影する。 */
    public record HeardSound(long sequence, long serverTick, double x, double y, double z,
            String soundId, String category, float volume, Float pitch, UUID sourceEntityId) {}
    public record Hearing(boolean observed, long serverTick, List<HeardSound> sounds, boolean truncated) {
        public Hearing { sounds = List.copyOf(sounds); }
        public static Hearing unavailable() { return new Hearing(false,-1,List.of(),false); }
    }
    public record Frame(JsonObject observation, Hearing hearing) {
        static Frame unavailable(String reason) { return new Frame(BodyConnection.unavailable(reason),Hearing.unavailable()); }
    }
    interface Source { Frame capture(UUID owner, String dimension); }
    private Source source;
    private boolean resolved;
    private String reason = "not_installed";

    /** client threadでentrypointを一度解決。古いAPIのクラスをロードする前に版の入口を検査する。 */
    private void resolve() {
        if (resolved) return;
        resolved = true;
        FabricLoader loader = FabricLoader.getInstance();
        if (!loader.isModLoaded("dogido_experiment")) return;
        reason = "version_mismatch";
        if (loader.getEntrypointContainers("dogido:body_bridge_v5",Object.class).isEmpty()) return;
        try { source = BodySnapshotReader.open(); }
        catch (RuntimeException | LinkageError error) { source = null; }
    }
    static JsonObject unavailable(String reason) {
        JsonObject json = new JsonObject();
        json.addProperty("version",3);
        json.addProperty("status","unavailable");
        json.addProperty("reason",reason);
        return json;
    }
    public boolean supported() { resolve(); return source != null; }
    /** client tickから実ownerと世界を受け、未導入・版違い・通信失敗も座標なしの契約で返す。 */
    public JsonObject capture(UUID owner, String dimension) {
        return captureFrame(owner,dimension).observation();
    }
    public Frame captureFrame(UUID owner, String dimension) {
        resolve();
        if (source == null) return Frame.unavailable(reason);
        try { return source.capture(owner,dimension); }
        catch (RuntimeException | LinkageError error) { return Frame.unavailable("bridge_error"); }
    }
}
