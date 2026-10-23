package dogido.lab.api;

import java.util.UUID;
import java.util.List;

/** 身体の実測と操作を不変データで渡す。HP/食料は身体の情報で、player観測や嗅覚へ混入させない。 */
public final class BodyProtocol {
    public static final int VERSION = 11;
    public static final String ENTRYPOINT = "dogido:body_bridge_v11";
    /** FOLLOWが高さを合わせ直す幅（block）。身体とadapterの停止位置計算で共有する。 */
    public static final double FOLLOW_HEIGHT_DISTANCE = 4;
    /** ownerの目の高さと身体の実寸から、通常追従する身体底面の高さを足元基準で返す。 */
    public static double followBodyHeight(double ownerEyeHeight, double bodySize) {
        return Math.max(.1,ownerEyeHeight-.2-bodySize/2);
    }
    /** 観察到着の許容.4blockと到着後の浮遊全幅.36blockに余白を足し、途中で高さ追従へ切り替わらない停止先に限定する。 */
    public static boolean inspectionHeightAllowed(double offsetFromFollowHeight) {
        return Double.isFinite(offsetFromFollowHeight) && Math.abs(offsetFromFollowHeight) <= FOLLOW_HEIGHT_DISTANCE-.8+1e-9;
    }
    private BodyProtocol() {}

    public enum Mode { DIRECT, FOLLOW, EXPLORE, HOLD }
    public enum Activity { UNBOUND, OWNER_ABSENT, HOLDING, FOLLOWING, EXPLORING, CALLING, RETREATING, MOVING, RESTING, DOWNED }
    public enum Presence { PRESENT, NOT_BOUND, UNLOADED, OWNER_UNAVAILABLE }
    public enum Action { CALL, MOVE_TO, LOOK_AT, REST, WAIT, EAT }
    /** MOVE_TOの目的。ownerへの接近先は身体が現在のowner姿勢から計算し、外部座標を受けない。 */
    public enum MovePurpose { INSPECT, OWNER_NEAR, OWNER_FACE }
    public enum Outcome { ACCEPTED, SUCCEEDED, REJECTED, INTERRUPTED, EXPIRED }
    public enum Submission { QUEUED, UNAVAILABLE, WRONG_THREAD, STALE_CONTEXT }
    public enum Sensors { BODY_VISION_AND_HEARING }
    public enum Visibility { VISIBLE, NOT_VISIBLE, UNAVAILABLE }
    public enum Life { ACTIVE, DOWNED }
    public enum ObservationStatus { OBSERVED, UNAVAILABLE }
    public enum FoodKind { DROPPED_ITEM, PLANT }

    /** Epoch changes on owner reconnect/respawn, body load, or dimension transfer. */
    public record Context(UUID ownerId, UUID bodyId, UUID epoch, String dimension) {}
    public record Position(double x, double y, double z) {}
    /** workingはadapterの既存採掘中観測。観測tickから20tickまで配置判断に使う。 */
    public record OwnerWorkObservation(Context context, long observedServerTick, Boolean working) {}
    /** 発話中に観測した注視点。block座標、nullは終了。sequenceは接続内単調増加、期限は観測tickから20tick。 */
    public record SpeechGazeObservation(Context context, long sequence, long observedServerTick, Position target) {}

    /** observedServerTick bounds transport age; basedOnSnapshot is traceability, not equality. */
    public record StateRequest(Context context, long revision, long basedOnSnapshot,
            long observedServerTick, int leaseTicks, boolean enabled, Mode mode, boolean retreat) {}
    /** LOOK_ATのdestinationは注視点（block）。移動せず最大100tick保持し、向きが合ってから成功を返す。 */
    public record ActionRequest(Context context, long sequence, UUID actionId,
            long observedServerTick, int validForTicks, Action action, Position destination, String foodId, MovePurpose movePurpose) {
        public ActionRequest(Context context, long sequence, UUID actionId, long observedServerTick,
                int validForTicks, Action action, Position destination, String foodId) {
            this(context,sequence,actionId,observedServerTick,validForTicks,action,destination,foodId,MovePurpose.INSPECT);
        }
        public ActionRequest(Context context, long sequence, UUID actionId, long observedServerTick,
                int validForTicks, Action action) {
            this(context,sequence,actionId,observedServerTick,validForTicks,action,null,null);
        }
    }
    /** 所有者と接続に束縛した明示CALL。身体が未生成でも、最新snapshotのtickを必須にする。 */
    public record RecallRequest(UUID ownerId, UUID connectionId, UUID actionId, String dimension,
            long observedServerTick, int validForTicks) {}
    /** 受付と完了を分離する。resolvedContextは実際に所有確認した身体からだけ取得する。 */
    public record RecallReceipt(UUID ownerId, UUID connectionId, UUID actionId, String dimension,
            Context resolvedContext, ActionResult result) {}
    public record StateResult(long revision, Outcome outcome, String reason) {}
    public record ActionResult(UUID actionId, long sequence, Outcome outcome, String reason) {}
    /** tickは同じサーバー稼働中の値。sequenceは個体内で保存し、同じ被弾の再配送を識別する。 */
    public record DamageEvent(long sequence, long serverTick, float amount, String cause) {}
    public record Vitals(Life life, float health, float maxHealth, boolean damageEnabled, String ownerGameMode,
            int wallTicks, int restTicks, int respawnRemainingTicks, long respawnCount, DamageEvent lastDamage) {}
    public record FoodTarget(String id, FoodKind kind, Position position, String itemId, int nutrition) {}
    /** 食料だけの見通し観測。一般視認・嗅覚・構造物センサーの実装済みを意味しない。 */
    public record FoodObservation(ObservationStatus status, long serverTick, List<FoodTarget> targets, boolean truncated) {
        public FoodObservation { targets = List.copyOf(targets); }
    }

    /** 同一tickの眼・owner足元と向き。座標はblock、角度は度でpitchの正は下。未取得はnull。 */
    public record PoseObservation(Position eyePosition, Position ownerPosition,
            float viewYaw, float viewPitch, float ownerYaw, float ownerPitch) {}
    /** owner一人の幾何視認。tickはSnapshotと一致、FOVは全角。未取得はUNAVAILABLEと理由で示す。 */
    public record VisionObservation(long serverTick, double rangeBlocks,
            double horizontalFovDegrees, double verticalFovDegrees, Visibility playerVisibility, String reason) {}

    /** 発音時の身体位置へ届いた実音。tick/sequenceは再配送で更新しない。pitch=nullはserver未取得（爆発）。 */
    public record HeardSound(long sequence, long serverTick, Position position, String soundId, String category,
            float volume, Float pitch, UUID sourceEntityId) {}
    /** serverの実音発生経路を採取した結果。空配列は世界の無音を保証せず、client固有音も別途補完される。 */
    public record HearingObservation(ObservationStatus status, long serverTick, List<HeardSound> sounds, boolean truncated) {
        public HearingObservation { sounds = List.copyOf(sounds); }
    }

    /** 保存しない安心度0..100と同tickのowner実測。距離はblock、可視性は身体の眼を基準とする。HPとは独立。 */
    public record Reassurance(double value, double ownerDistance, boolean ownerVisible, boolean faceVisible, boolean ownerWorking) {}
    /** 現在の身体側制御状態。観測した値であり、adapterから自律移動を有効化しない。 */
    public record ControlObservation(boolean enabled, Mode mode, double followDistance, double warpDistance, Reassurance reassurance) {}

    /** 身体が未ロードならpositionとcontrolはnull。未取得の座標・制御・安心度を補完しない。 */
    public record Snapshot(int version, Presence presence, Context context, String ownerDimension, long serverTick,
            long snapshotRevision, Position position, float yaw, Activity activity, String reason,
            long appliedStateRevision, StateResult stateResult, ActionResult actionResult, Sensors sensors,
            Vitals vitals, FoodObservation food, PoseObservation pose, VisionObservation vision, HearingObservation hearing, ControlObservation control) {}
}
