package dogido.lab.api;

import java.util.UUID;
import java.util.List;

/** 身体の実測と操作を不変データで渡す。HP/食料は身体の情報で、player観測や嗅覚へ混入させない。 */
public final class BodyProtocol {
    public static final int VERSION = 4;
    public static final String ENTRYPOINT = "dogido:body_bridge_v4";
    private BodyProtocol() {}

    public enum Mode { DIRECT, FOLLOW, EXPLORE, HOLD }
    public enum Activity { UNBOUND, OWNER_ABSENT, HOLDING, FOLLOWING, EXPLORING, CALLING, RETREATING, MOVING, RESTING, DOWNED }
    public enum Presence { PRESENT, NOT_BOUND, UNLOADED, OWNER_UNAVAILABLE }
    public enum Action { CALL, MOVE_TO, REST, WAIT, EAT }
    public enum Outcome { ACCEPTED, SUCCEEDED, REJECTED, INTERRUPTED, EXPIRED }
    public enum Submission { QUEUED, UNAVAILABLE, WRONG_THREAD, STALE_CONTEXT }
    public enum Sensors { OWNER_VISION_ONLY }
    public enum Visibility { VISIBLE, NOT_VISIBLE, UNAVAILABLE }
    public enum Life { ACTIVE, DOWNED }
    public enum ObservationStatus { OBSERVED, UNAVAILABLE }
    public enum FoodKind { DROPPED_ITEM, PLANT }

    /** Epoch changes on owner reconnect/respawn, body load, or dimension transfer. */
    public record Context(UUID ownerId, UUID bodyId, UUID epoch, String dimension) {}
    public record Position(double x, double y, double z) {}

    /** observedServerTick bounds transport age; basedOnSnapshot is traceability, not equality. */
    public record StateRequest(Context context, long revision, long basedOnSnapshot,
            long observedServerTick, int leaseTicks, boolean enabled, Mode mode, boolean retreat) {}
    public record ActionRequest(Context context, long sequence, UUID actionId,
            long observedServerTick, int validForTicks, Action action, Position destination, String foodId) {
        public ActionRequest(Context context, long sequence, UUID actionId, long observedServerTick,
                int validForTicks, Action action) {
            this(context,sequence,actionId,observedServerTick,validForTicks,action,null,null);
        }
    }
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

    /** position is null when the body is not loaded, never a synthetic origin position. */
    public record Snapshot(int version, Presence presence, Context context, String ownerDimension, long serverTick,
            long snapshotRevision, Position position, float yaw, Activity activity, String reason,
            long appliedStateRevision, StateResult stateResult, ActionResult actionResult, Sensors sensors,
            Vitals vitals, FoodObservation food, PoseObservation pose, VisionObservation vision) {}
}
