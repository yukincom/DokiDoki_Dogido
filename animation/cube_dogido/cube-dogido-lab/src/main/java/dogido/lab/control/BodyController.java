package dogido.lab.control;

import dogido.lab.api.BodyProtocol.*;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import java.util.UUID;
import net.minecraft.util.math.Vec3d;

/** 身体の目標だけを選ぶ。FOLLOWは危険回避と持ち主の隣までの追従を扱い、会話判断やワールド操作は所有しない。 */
public final class BodyController {
    public record Owner(Vec3d feet, float yaw, double bodyHeight, boolean working) {
        public Owner(Vec3d feet, float yaw, double bodyHeight) { this(feet,yaw,bodyHeight,false); }
    }
    @FunctionalInterface public interface TargetSpace { boolean clear(Vec3d position, boolean allowWall); }
    public record Plan(Vec3d target, Activity activity, String reason, boolean allowWall) {}
    public record InspectionResult(UUID id, Outcome outcome, String reason) {}
    public record CallContinuation(UUID id, long started) {}
    public static final int INSPECTION_MAX_TICKS = 600;
    private final Random random;
    private final FollowAttention attention;
    private boolean idleAttention;
    private Vec3d target, anchorOwner;
    private long nextTarget, callStarted;
    private UUID callId;
    private Activity previous = Activity.HOLDING;
    private boolean callFinished;
    private String callEnd;
    private boolean following;
    private Vec3d followOffset;
    private UUID inspectionId;
    private Vec3d inspectionTarget, inspectionOwnerOffset;
    private MovePurpose inspectionPurpose = MovePurpose.INSPECT, dwellPurpose = MovePurpose.INSPECT;
    private long faceDwellUntil = -1, ownerReturnUntil = -1;
    private long inspectionExpires;
    private boolean inspectionStarted;
    private InspectionResult inspectionResult;

    public BodyController(long seed) { random = new Random(seed); attention = new FollowAttention(); }
    public void reset() {
        target = anchorOwner = null; callId = null; nextTarget = callStarted = 0;
        callFinished = false; callEnd = null; previous = Activity.HOLDING;
        following = false; followOffset = null;
        inspectionId = null; inspectionTarget = inspectionOwnerOffset = null; inspectionResult = null; inspectionStarted = false;
        inspectionPurpose = dwellPurpose = MovePurpose.INSPECT; faceDwellUntil = ownerReturnUntil = -1;
        attention.reset(); idleAttention = false;
    }
    public void call(UUID id, Owner owner, long tick) {
        if (id.equals(callId)) return;
        cancelInspection("call_has_priority");
        following = false; followOffset = null;
        callId = id; callStarted = tick; callFinished = false; callEnd = null; target = null;
    }
    public void cancelCall(String reason) {
        if (callId != null) { callEnd = reason; callId = null; target = null; }
    }
    public boolean takeArrived() { boolean value = callFinished; callFinished = false; return value; }
    public String takeCallEnd() { String value = callEnd; callEnd = null; return value; }
    public boolean calling() { return callId != null; }
    public boolean following() { return following; }
    /** ワープ後の有効なローカルFOLLOWだけが呼ぶ。着地点の距離にかかわらず、隣への接近を再開する。 */
    public void resumeFollowing() { following = true; followOffset = null; }
    public CallContinuation continuation() { return callId == null ? null : new CallContinuation(callId,callStarted); }
    /** 同じserver内の距離warp後に、既存の有効期限を引き継ぐ。新しい世代で再受理された同一CALLだけが対象。 */
    public void retainCallDeadline(CallContinuation value) {
        if (value != null && value.id().equals(callId)) callStarted = value.started();
    }

    /** 検証済みの見に行く座標を保持する。再送で期限を延長せず、実移動時にも優先度と安全性を確認する。 */
    public void inspect(UUID id, Vec3d destination, long expiresAt) {
        inspect(id,destination,MovePurpose.INSPECT,null,expiresAt);
    }
    /** owner接近は受理時の相対位置を持つ。移動は現在ownerに平行移動し、視線だけの回転には追従しない。 */
    public void inspect(UUID id, Vec3d destination, MovePurpose purpose, Owner owner, long expiresAt) {
        if (purpose == null || purpose != MovePurpose.INSPECT && owner == null)
            throw new IllegalArgumentException("owner_approach_requires_owner");
        if (id == null || destination == null || !Double.isFinite(destination.x)
                || !Double.isFinite(destination.y) || !Double.isFinite(destination.z))
            throw new IllegalArgumentException("finite_inspection_target_required");
        if (id.equals(inspectionId)) return;
        cancelInspection("inspection_replaced");
        inspectionId = id; inspectionTarget = destination; inspectionExpires = expiresAt; inspectionStarted = false;
        inspectionPurpose = purpose;
        inspectionOwnerOffset = purpose == MovePurpose.INSPECT ? null
            : destination.subtract(owner.feet()).subtract(0,owner.bodyHeight(),0);
    }
    public void cancelInspection(String reason) {
        finishInspection(Outcome.INTERRUPTED,reason); attention.reset(); faceDwellUntil = ownerReturnUntil = -1; dwellPurpose = MovePurpose.INSPECT;
    }
    public boolean approachingOwner() { return inspectionId != null && inspectionPurpose != MovePurpose.INSPECT; }
    /** 顔の接近先へ実到着した後の5秒だけ目線を合わせる。発話中の注視はEntity側が優先する。 */
    public boolean lookingAtOwner(long tick) { return idleAttention && attention.dwelling(tick) && tick < faceDwellUntil; }
    /** 接近命令の受付とRust向けreasonの両方で、実到着後の間を守る。 */
    public boolean inspectionDwelling(long tick) { return attention.dwelling(tick); }
    public boolean attentionSettling(long tick) { return attention.settling(tick); }
    /** 実帰還後の間だけ、顔を見る次手を受け付ける。初回待機・高速移動・顔や見物への到着では許可しない。 */
    public String inspectionPauseReason(MovePurpose purpose, long tick) {
        if (purpose == MovePurpose.OWNER_FACE && tick >= 0 && tick < ownerReturnUntil) return null;
        if (attention.dwelling(tick)) return "inspection_dwell";
        return attention.settling(tick) ? "follow_settling" : null;
    }
    public InspectionResult takeInspectionResult() {
        InspectionResult value = inspectionResult; inspectionResult = null; return value;
    }
    private void finishInspection(Outcome outcome, String reason) {
        if (inspectionId != null) inspectionResult = new InspectionResult(inspectionId,outcome,reason);
        inspectionId = null; inspectionTarget = inspectionOwnerOffset = null; inspectionStarted = false;
        inspectionPurpose = MovePurpose.INSPECT;
    }

    public Plan plan(Vec3d body, Owner owner, long tick, boolean enabled, Mode mode, boolean retreat,
            BodyTuning tuning, TargetSpace space) {
        return plan(body,owner,tick,enabled,mode,retreat,tuning,space,BodySafety.EMPTY);
    }

    /** 同じ危険観測を目標選択と実移動の両方へ渡し、待機中もFOLLOWの退避を優先する。 */
    public Plan plan(Vec3d body, Owner owner, long tick, boolean enabled, Mode mode, boolean retreat,
            BodyTuning tuning, TargetSpace space, BodySafety safety) {
        idleAttention = false;
        if (owner == null || !enabled) {
            cancelCall(owner == null ? "owner_unavailable" : "control_inactive");
            cancelInspection(owner == null ? "owner_unavailable" : "control_inactive");
            following = false; followOffset = null;
            target = null; previous = owner == null ? Activity.OWNER_ABSENT : Activity.HOLDING;
            return new Plan(body,previous,owner == null ? "owner_unavailable" : "control_inactive",true);
        }
        if (mode != Mode.FOLLOW) { cancelInspection("control_mode_changed"); following = false; followOffset = null; }
        if (retreat) cancelInspection("retreat_has_priority");
        if (callId != null) cancelInspection("call_has_priority");
        if (inspectionId != null) {
            if (!inspectionStarted) { inspectionExpires = Math.min(inspectionExpires,tick+INSPECTION_MAX_TICKS); inspectionStarted = true; }
            if (tick >= inspectionExpires) finishInspection(Outcome.EXPIRED,"inspection_expired");
        }
        if (retreat) cancelCall("retreat_has_priority");
        if (callId != null && tick-callStarted >= BodyTuning.CALL_TIMEOUT_TICKS) cancelCall("call_timeout");
        if (mode == Mode.FOLLOW) {
            // 危険回避が先に走るtickでも距離超過を覚え、安全になったら隣まで戻る。
            if (!retreat && callId == null && (body.distanceTo(owner.feet()) > tuning.emergencyFollowDistance()
                    || Math.abs(body.y-owner.feet().y-owner.bodyHeight()) > BodyTuning.FOLLOW_HEIGHT_DISTANCE)) following = true;
            Plan avoidance = avoid(body,space,safety);
            if (avoidance != null) {
                cancelInspection("danger_has_priority");
                target = null; previous = avoidance.activity(); return avoidance;
            }
            if (!retreat && callId == null) return follow(body,owner,tick,tuning,space,safety);
        }
        if (callId != null) return approach(body,owner,tick,tuning,space,safety);
        Activity activity = retreat ? Activity.RETREATING : callId != null ? Activity.CALLING
            : mode == Mode.EXPLORE ? Activity.EXPLORING : mode == Mode.FOLLOW ? Activity.FOLLOWING : Activity.HOLDING;
        if (activity == Activity.HOLDING) { target = null; previous = activity; return new Plan(body,activity,"hold",true); }
        boolean moved = anchorOwner == null || owner.feet().distanceTo(anchorOwner) > (activity == Activity.EXPLORING ? 2 : .75);
        boolean renew = target == null || previous != activity || moved || tick >= nextTarget
            || !space.clear(target,activity == Activity.RETREATING);
        if (activity == Activity.EXPLORING && previous == activity && !moved && target != null
                && body.distanceTo(target) < .4 && tick < nextTarget) renew = false;
        boolean allowWall = false;
        if (renew) {
            List<Vec3d> options = candidates(activity,owner,tuning);
            target = select(options,space,false);
            if (target == null && activity == Activity.RETREATING) { target = select(options,space,true); allowWall = true; }
            anchorOwner = owner.feet();
            nextTarget = tick + (activity == Activity.EXPLORING ? BodyTuning.EXPLORE_WAIT_TICKS+random.nextInt(101) : BodyTuning.TARGET_HOLD_TICKS);
        } else if (activity == Activity.RETREATING && target != null) allowWall = !space.clear(target,false);
        previous = activity;
        if (target == null) return new Plan(body,activity,"no_safe_target",activity == Activity.RETREATING);
        // Follow owner translation during a held target, without orbiting at every camera turn.
        if (activity != Activity.EXPLORING && !renew) {
            Vec3d shifted = target.add(owner.feet().subtract(anchorOwner));
            if (space.clear(shifted,allowWall)) { target = shifted; anchorOwner = owner.feet(); }
        }
        return new Plan(target,activity,allowWall ? "wall_refuge" : "moving",allowWall);
    }

    /** 現在位置から安全な到着先を選ぶ。実到着したtickでCALLだけを終え、成功を一度返して通常のmodeへ戻す。 */
    private Plan approach(Vec3d body, Owner owner, long tick, BodyTuning tuning, TargetSpace space, BodySafety safety) {
        target = CallPlacement.candidates(owner,tuning.callDistance,body).stream()
            .filter(p -> space.clear(p,false) && safety.safeTarget(p)).findFirst().orElse(null);
        previous = Activity.CALLING;
        if (target == null) return new Plan(body,Activity.CALLING,"no_safe_target",false);
        if (body.distanceTo(target) <= .2 && body.subtract(owner.feet()).horizontalLength() <= tuning.callDistance
                && space.clear(body,false) && safety.safeTarget(body)) {
            // 狭所の候補では到着許容距離がownerとの隙間より大きいことがある。目標だけでなく実位置も再照合する。
            // 到着成功を取消結果で上書きせず、ownerの向き・小移動への張り付きもここで終える。
            callFinished = true; callId = null; target = null; previous = Activity.HOLDING;
            return new Plan(body,Activity.HOLDING,"call_arrived",false);
        }
        return new Plan(target,Activity.CALLING,"moving",false);
    }

    /** 開始距離を越えたら現在のownerの隣まで追従する。到着は安全な目標への実接近で確定し、カメラ回転だけでは周回しない。 */
    private Plan follow(Vec3d body, Owner owner, long tick, BodyTuning tuning, TargetSpace space, BodySafety safety) {
        previous = following ? Activity.FOLLOWING : Activity.HOLDING;
        target = null;
        if (!following) {
            attention.observeOwner(tick,owner.feet());
            if (owner.working()) faceDwellUntil = -1;
            if (inspectionId != null) {
                boolean ownerApproach = approachingOwner();
                if (ownerApproach) {
                    if (inspectionPurpose == MovePurpose.OWNER_FACE && owner.working()) {
                        cancelInspection("owner_working");
                    } else {
                        Vec3d destination = owner.feet().add(inspectionOwnerOffset.x,
                            owner.bodyHeight()+inspectionOwnerOffset.y,inspectionOwnerOffset.z);
                        if (!space.clear(destination,false) || !safety.safeTarget(destination)
                                || owner.working() && BodyMotion.nearFront(destination,owner.feet(),owner.yaw())) {
                            destination = ownerDestination(owner,body,inspectionPurpose,space,safety);
                            if (destination == null) cancelInspection("owner_approach_target_unsafe");
                            else inspectionOwnerOffset = destination.subtract(owner.feet()).subtract(0,owner.bodyHeight(),0);
                        }
                        if (inspectionId != null) inspectionTarget = destination;
                    }
                } else if (body.distanceTo(owner.feet()) > tuning.followDistance
                        || inspectionTarget.distanceTo(owner.feet()) > tuning.followDistance) {
                    cancelInspection("inspection_outside_follow_distance");
                } else if (!dogido.lab.api.BodyProtocol.inspectionHeightAllowed(inspectionTarget.y-owner.feet().y-owner.bodyHeight())) {
                    cancelInspection("inspection_outside_follow_height");
                }
                if (inspectionId != null) {
                    if (!space.clear(inspectionTarget,false) || !safety.safeTarget(inspectionTarget)) {
                        cancelInspection("inspection_target_unsafe");
                    } else if (body.distanceTo(inspectionTarget) <= (ownerApproach ? .2 : .4)
                            && space.clear(body,false) && safety.safeTarget(body)) {
                        boolean face = inspectionPurpose == MovePurpose.OWNER_FACE;
                        ownerReturnUntil = inspectionPurpose == MovePurpose.OWNER_NEAR ? tick+FollowAttention.DWELL_TICKS : -1;
                        dwellPurpose = inspectionPurpose;
                        finishInspection(Outcome.SUCCEEDED,"arrived");
                        attention.arrived(tick);
                        faceDwellUntil = face ? tick+FollowAttention.DWELL_TICKS : -1;
                    } else return new Plan(inspectionTarget,Activity.MOVING,ownerApproach ? inspectionPurpose == MovePurpose.OWNER_FACE ? "owner_face" : "owner_near" : "inspecting",false);
                }
            }
            idleAttention = true;
            return new Plan(body,Activity.HOLDING,attention.dwelling(tick) ? dwellReason()
                : attention.settling(tick) ? "follow_settling" : "within_follow_distance",false);
        }
        cancelInspection("distance_follow_has_priority");
        // 選んだ水平位置はownerの移動に平行移動させ、高さは現在の目線へ合わせる。視線だけが変わっても周回させず、
        // 作業正面へ入る・障害物や敵で塞がれる場合に限り、既存の立ち位置候補から選び直す。
        Vec3d destination = followOffset == null ? null
            : owner.feet().add(followOffset.x,owner.bodyHeight()+followOffset.y,followOffset.z);
        if (destination == null || !space.clear(destination,false) || !safety.safeTarget(destination)
                || owner.working() && BodyMotion.nearFront(destination,owner.feet(),owner.yaw())) {
            destination = CallPlacement.besideCandidates(owner,BodyTuning.FOLLOW_ARRIVAL_DISTANCE).stream()
                .filter(p -> space.clear(p,false) && safety.safeTarget(p))
                .min(java.util.Comparator.comparingDouble(p -> p.subtract(body).horizontalLengthSquared()))
                .orElse(null);
            // 開けた場所では従来の横／後ろを優先。全滅した時だけ狭所候補を順に検査し、常時頭上へ寄らない。
            if (destination == null) destination = CallPlacement.fallbackCandidates(owner,BodyTuning.FOLLOW_ARRIVAL_DISTANCE,body).stream()
                .filter(p -> space.clear(p,false) && safety.safeTarget(p)).findFirst().orElse(null);
            followOffset = destination == null ? null : destination.subtract(owner.feet()).subtract(0,owner.bodyHeight(),0);
        }
        if (destination == null) return new Plan(body,Activity.FOLLOWING,"no_safe_target",false);
        if (body.distanceTo(destination) <= .2
                && body.subtract(owner.feet()).horizontalLength() <= BodyTuning.FOLLOW_ARRIVAL_DISTANCE
                && space.clear(body,false) && safety.safeTarget(body)) {
            following = false; followOffset = null; previous = Activity.HOLDING; idleAttention = true;
            attention.observeOwner(tick,owner.feet());
            ownerReturnUntil = tick+FollowAttention.SETTLE_TICKS;
            return new Plan(body,Activity.HOLDING,"follow_settling",false);
        }
        return new Plan(destination,Activity.FOLLOWING,"distance_follow",false);
    }

    private String dwellReason() {
        return switch (dwellPurpose) {
            case OWNER_NEAR -> "owner_near_dwell";
            case OWNER_FACE -> "owner_face_dwell";
            case INSPECT -> "inspection_dwell";
        };
    }

    /**
     * ownerの現在姿勢から安全な接近位置だけを返す。顔は斜め前、そばは横・後ろを先に調べる。
     * 候補が無い場合はnullとし、顔が見えた／到着したという結果は作らない。
     */
    public static Vec3d ownerDestination(Owner owner, Vec3d body, MovePurpose purpose, TargetSpace space, BodySafety safety) {
        if (owner == null || purpose == MovePurpose.INSPECT || purpose == null) return null;
        List<Vec3d> candidates = purpose == MovePurpose.OWNER_FACE
            ? CallPlacement.faceCandidates(owner,BodyTuning.FOLLOW_ARRIVAL_DISTANCE)
            : CallPlacement.besideCandidates(owner,BodyTuning.FOLLOW_ARRIVAL_DISTANCE);
        Vec3d destination = candidates.stream().filter(p -> space.clear(p,false) && safety.safeTarget(p))
            .min(java.util.Comparator.comparingDouble(p -> p.subtract(body).horizontalLengthSquared())).orElse(null);
        if (destination == null && purpose == MovePurpose.OWNER_NEAR)
            destination = CallPlacement.fallbackCandidates(owner,BodyTuning.FOLLOW_ARRIVAL_DISTANCE,body).stream()
                .filter(p -> space.clear(p,false) && safety.safeTarget(p)).findFirst().orElse(null);
        return destination;
    }

    /** 水平の逃げ道が閉じた場合に上昇を先に試す。敵の予測経路を横切る上昇は候補にしない。 */
    private static Plan avoid(Vec3d body, TargetSpace space, BodySafety safety) {
        if (safety.isEmpty()) return null;
        List<Vec3d> horizontal = new ArrayList<>();
        boolean horizontalClosed = true;
        for (int i = 0; i < 16; i++) {
            double angle = i*Math.PI/8;
            Vec3d delta = new Vec3d(Math.cos(angle)*2.5,0,Math.sin(angle)*2.5);
            if (safety.safeTarget(body.add(delta)) && safety.allowsStep(body,delta)) horizontalClosed = false;
            for (double radius : new double[]{2.5,4,6}) horizontal.add(body.add(delta.multiply(radius/2.5)));
        }
        if (!safety.threatened(body) && !horizontalClosed) return null;
        List<Vec3d> vertical = new ArrayList<>();
        for (double rise : new double[]{2.5,4,6}) vertical.add(body.add(0,rise,0));
        List<Vec3d> candidates = new ArrayList<>();
        if (horizontalClosed) candidates.addAll(vertical);
        candidates.addAll(horizontal);
        if (!horizontalClosed) candidates.addAll(vertical);
        for (Vec3d candidate : candidates) {
            if (space.clear(candidate,false) && safety.safeTarget(candidate)
                    && safety.allowsStep(body,candidate.subtract(body)))
                return new Plan(candidate,Activity.RETREATING,candidate.y > body.y ? "avoid_enemy_upward" : "avoid_enemy_sideways",false);
        }
        return new Plan(body,Activity.RETREATING,"no_safe_target",false);
    }

    private List<Vec3d> candidates(Activity activity, Owner owner, BodyTuning tuning) {
        List<Vec3d> result = new ArrayList<>();
        if (activity == Activity.EXPLORING) {
            for (int i = 0; i < 24; i++) {
                double radius = tuning.exploreMin + random.nextDouble()*(tuning.exploreMax-tuning.exploreMin);
                result.add(BodyMotion.offset(owner.feet(),random.nextFloat()*360,radius,0,owner.bodyHeight()+random.nextDouble()*.8));
            }
        } else {
            double distance = -tuning.retreatDistance;
            // A nearby enemy can exclude the preferred spot; keep looking farther behind.
            for (double extra : activity == Activity.RETREATING ? new double[]{0,3,6} : new double[]{0})
                for (double dy : new double[]{0,.6,-.6})
                    for (double side : new double[]{0,1.5,-1.5,3,-3})
                        result.add(BodyMotion.offset(owner.feet(),owner.yaw(),distance-extra,side,owner.bodyHeight()+dy));
        }
        if (activity != Activity.CALLING) result.removeIf(p -> BodyMotion.nearFront(p,owner.feet(),owner.yaw()));
        return result;
    }
    private static Vec3d select(List<Vec3d> options, TargetSpace space, boolean walls) {
        return options.stream().filter(p -> space.clear(p,walls)).findFirst().orElse(null);
    }
}
