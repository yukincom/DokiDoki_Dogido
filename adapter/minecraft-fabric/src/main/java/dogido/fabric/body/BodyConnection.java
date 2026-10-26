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
    public record Frame(JsonObject observation, Hearing hearing, JsonObject autonomy, JsonObject lookResults) {
        public JsonObject lookResult(UUID actionId) { return lookResults==null?null:lookResults.getAsJsonObject(actionId.toString()); }
        static Frame unavailable(String reason) { return new Frame(BodyConnection.unavailable(reason),Hearing.unavailable(),BodyInspectProtocol.unavailable(reason),null); }
    }
    public record MoveRequest(BodyInspectProtocol.Context context, long sequence, UUID actionId,
            long observedServerTick, int validForTicks, BodyInspectProtocol.Point destination, BodyInspectProtocol.Purpose purpose) {}
    /** 注視点は移動先ではない。同じaction配送で所有者・世代・期限・重複を検査する。 */
    public record LookRequest(BodyInspectProtocol.Context context,long sequence,UUID actionId,
            long observedServerTick,int validForTicks,BodyInspectProtocol.Point target) {}
    public record RecallRequest(UUID ownerId,UUID connectionId,UUID actionId,String dimension) {}
    public record RecallResult(UUID ownerId,UUID connectionId,UUID actionId,String dimension,
            BodyInspectProtocol.Context context,long sequence,String outcome,String reason) {}
    interface Source {
        Frame capture(UUID owner, String dimension);
        default String recall(RecallRequest request) { return "unavailable"; }
        default RecallResult recallResult(UUID actionId) { return null; }
        default String lookAt(LookRequest request) { return "unavailable"; }
        default String moveTo(MoveRequest request) { return "unavailable"; }
        default void observeOwnerWork(boolean working) {}
        default void observeSpeechGaze(BodyInspectProtocol.Context context,long sequence,long tick,net.minecraft.util.math.Vec3d point) {}
    }
    private Source source;
    private boolean resolved;
    private BodyInspectProtocol.Context sequenceContext;
    private long lastActionSequence;
    private enum ActionKind { MOVE, LOOK, RECALL }
    private UUID activeAction, activeLook;
    private ActionKind activeKind;
    private long activeUntil;
    /** 移動と注視で共有sequenceを現在context内で進める。送信中の移動は注視を妨げない。 */
    public synchronized long nextActionSequence(BodyInspectProtocol.Context context,long prior,long tick) {
        if (!java.util.Objects.equals(sequenceContext,context)) { sequenceContext=context; lastActionSequence=0; }
        lastActionSequence=Math.max(Math.max(lastActionSequence,prior),tick)+1;
        return lastActionSequence;
    }
    private synchronized boolean claim(UUID id,ActionKind kind) {
        if(kind==ActionKind.LOOK) { activeLook=id;return true; }
        if (activeAction != null && !activeAction.equals(id) && System.nanoTime() < activeUntil
                && (kind != ActionKind.RECALL || activeKind == ActionKind.RECALL)) return false;
        // 明示CALLは既存身体の優先操作。自律MOVE_TOや注視のadapter枠で妨げない。
        activeAction=id; activeKind=kind; activeUntil=System.nanoTime()+35_000_000_000L; return true;
    }
    public synchronized void release(UUID id) {
        if(java.util.Objects.equals(activeAction,id))activeAction=null;
        if(java.util.Objects.equals(activeLook,id))activeLook=null;
    }
    public synchronized void clearActions() { activeAction=activeLook=null; sequenceContext=null; lastActionSequence=0; }
    private String reason = "not_installed";

    /** client threadでentrypointを一度解決。古いAPIのクラスをロードする前に版の入口を検査する。 */
    private void resolve() {
        if (resolved) return;
        resolved = true;
        FabricLoader loader = FabricLoader.getInstance();
        if (!loader.isModLoaded("dogido_experiment")) return;
        reason = "version_mismatch";
        if (loader.getEntrypointContainers("dogido:body_bridge_v12",Object.class).isEmpty()) return;
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
        if (!claim(request.actionId(),ActionKind.MOVE)) return "body_action_pending";
        try { String result=source.moveTo(request); if (!"queued".equals(result)) release(request.actionId()); return result; }
        catch (RuntimeException | LinkageError error) { release(request.actionId()); return "bridge_error"; }
    }
    /** 明示分類または許可済み行動の実測注視点を既存LOOK_ATへ送る。 */
    public String lookAt(LookRequest request) {
        resolve();
        if (source == null) return "unavailable";
        if (!claim(request.actionId(),ActionKind.LOOK)) return "body_action_pending";
        try { String result=source.lookAt(request); if (!"queued".equals(result)) release(request.actionId()); return result; }
        catch (RuntimeException | LinkageError error) { release(request.actionId()); return "bridge_error"; }
    }
    public String recall(RecallRequest request) {
        resolve(); if (source == null) return "unavailable";
        if (!claim(request.actionId(),ActionKind.RECALL)) return "body_action_pending";
        try { String result=source.recall(request); if (!"queued".equals(result)) release(request.actionId()); return result; }
        catch (RuntimeException | LinkageError error) { release(request.actionId()); return "bridge_error"; }
    }
    public RecallResult recallResult(UUID actionId) {
        resolve(); if (source == null) return null;
        try { return source.recallResult(actionId); }
        catch (RuntimeException | LinkageError error) { return null; }
    }
    public Frame captureFrame(UUID owner, String dimension) {
        resolve();
        if (source == null) return Frame.unavailable(reason);
        try {
            Frame frame=source.capture(owner,dimension);
            UUID active; synchronized (this) { active=activeAction; }
            RecallResult recall=active == null ? null : source.recallResult(active);
            if (recall != null && !"accepted".equals(recall.outcome())) release(active);
            releaseCompleted(frame.autonomy().getAsJsonObject("action_result"));
            if(frame.lookResults()!=null)for(var result:frame.lookResults().entrySet())releaseCompleted(result.getValue().getAsJsonObject());
            return frame;
        }
        catch (RuntimeException | LinkageError error) { return Frame.unavailable("bridge_error"); }
    }
    private void releaseCompleted(JsonObject result) {
        if(result==null || "accepted".equals(BodyInspectProtocol.string(result,"outcome")))return;
        String id=BodyInspectProtocol.string(result,"action_id");
        if(id!=null)release(UUID.fromString(id));
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
