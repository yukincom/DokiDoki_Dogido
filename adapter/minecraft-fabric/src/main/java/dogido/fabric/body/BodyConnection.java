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
    public record Frame(JsonObject observation, Hearing hearing, JsonObject autonomy) {
        public Frame(JsonObject observation, Hearing hearing) { this(observation,hearing,BodyInspectProtocol.unavailable("not_sampled")); }
        static Frame unavailable(String reason) { return new Frame(BodyConnection.unavailable(reason),Hearing.unavailable(),BodyInspectProtocol.unavailable(reason)); }
    }
    public record MoveRequest(BodyInspectProtocol.Context context, long sequence, UUID actionId,
            long observedServerTick, int validForTicks, BodyInspectProtocol.Point destination, BodyInspectProtocol.Purpose purpose) {
        public MoveRequest(BodyInspectProtocol.Context context,long sequence,UUID actionId,
                long observedServerTick,int validForTicks,BodyInspectProtocol.Point destination) {
            this(context,sequence,actionId,observedServerTick,validForTicks,destination,BodyInspectProtocol.Purpose.INSPECT);
        }
    }
    interface Source {
        Frame capture(UUID owner, String dimension);
        default String moveTo(MoveRequest request) { return "unavailable"; }
        default void observeOwnerWork(boolean working) {}
        default void observeSpeechGaze(BodyInspectProtocol.Context context,long sequence,long tick,net.minecraft.util.math.Vec3d point) {}
    }
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
        if (loader.getEntrypointContainers("dogido:body_bridge_v9",Object.class).isEmpty()) return;
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
    /** 既存MOVE_TOだけを任意APIへ送る。DIRECTや追従の有効化をここから変更しない。 */
    public String moveTo(MoveRequest request) {
        resolve();
        if (source == null) return "unavailable";
        try { return source.moveTo(request); }
        catch (RuntimeException | LinkageError error) { return "bridge_error"; }
    }
    public Frame captureFrame(UUID owner, String dimension) {
        resolve();
        if (source == null) return Frame.unavailable(reason);
        try { return source.capture(owner,dimension); }
        catch (RuntimeException | LinkageError error) { return Frame.unavailable("bridge_error"); }
    }
    /** client tickで取得した既存作業観測を任意身体へ送る。未導入・版違いは通常観測を止めない。 */
    public void observeOwnerWork(boolean working) {
        resolve();
        if (source == null) return;
        try { source.observeOwnerWork(working); }
        catch (RuntimeException | LinkageError ignored) { }
    }
    /** 同じ任意APIに実再生中の注視点を渡す。nullは終了で、移動要求には変換しない。 */
    public void observeSpeechGaze(BodyInspectProtocol.Context context,long sequence,long tick,net.minecraft.util.math.Vec3d point) {
        resolve();
        if (source == null) return;
        try { source.observeSpeechGaze(context,sequence,tick,point); }
        catch (RuntimeException | LinkageError ignored) { }
    }
}
