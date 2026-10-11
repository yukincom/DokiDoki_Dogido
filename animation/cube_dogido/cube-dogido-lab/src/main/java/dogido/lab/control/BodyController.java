package dogido.lab.control;

import dogido.lab.api.BodyProtocol.*;
import java.util.ArrayList;
import java.util.List;
import java.util.Random;
import java.util.UUID;
import net.minecraft.util.math.Vec3d;

/** Target selection has no knowledge of dialogue, scent notifications or player combat policy. */
public final class BodyController {
    public record Owner(Vec3d feet, float yaw, double bodyHeight) {}
    @FunctionalInterface public interface TargetSpace { boolean clear(Vec3d position, boolean allowWall); }
    public record Plan(Vec3d target, Activity activity, String reason, boolean allowWall) {}
    private final Random random;
    private Vec3d target, anchorOwner, callOwner;
    private long nextTarget, callStarted, callArrived = -1;
    private UUID callId;
    private Activity previous = Activity.HOLDING;
    private boolean callFinished;
    private String callEnd;

    public BodyController(long seed) { random = new Random(seed); }
    public void reset() {
        target = anchorOwner = callOwner = null; callId = null; nextTarget = callStarted = 0;
        callArrived = -1; callFinished = false; callEnd = null; previous = Activity.HOLDING;
    }
    public void call(UUID id, Owner owner, long tick) {
        if (id.equals(callId)) return;
        callId = id; callOwner = owner.feet(); callStarted = tick; callArrived = -1; callFinished = false; callEnd = null; target = null;
    }
    public void cancelCall(String reason) {
        if (callId != null) { callEnd = reason; callId = null; target = null; }
    }
    public boolean takeArrived() { boolean value = callFinished; callFinished = false; return value; }
    public String takeCallEnd() { String value = callEnd; callEnd = null; return value; }
    public boolean calling() { return callId != null; }

    public Plan plan(Vec3d body, Owner owner, long tick, boolean enabled, Mode mode, boolean retreat,
            BodyTuning tuning, TargetSpace space) {
        if (owner == null || !enabled) {
            cancelCall(owner == null ? "owner_unavailable" : "control_inactive");
            target = null; previous = owner == null ? Activity.OWNER_ABSENT : Activity.HOLDING;
            return new Plan(body,previous,owner == null ? "owner_unavailable" : "control_inactive",true);
        }
        if (retreat) cancelCall("retreat_has_priority");
        if (callId != null && tick-callStarted >= BodyTuning.CALL_TIMEOUT_TICKS) cancelCall("call_timeout");
        if (callId != null && callArrived >= 0 && (tick-callArrived >= BodyTuning.CALL_HOLD_TICKS
                || owner.feet().distanceTo(callOwner) > 1.25)) cancelCall("call_hold_finished");
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
        if (activity == Activity.CALLING && callArrived < 0 && body.distanceTo(target) < .4) {
            callArrived = tick; callOwner = owner.feet(); callFinished = true;
        }
        return new Plan(target,activity,allowWall ? "wall_refuge" : "moving",allowWall);
    }

    private List<Vec3d> candidates(Activity activity, Owner owner, BodyTuning tuning) {
        List<Vec3d> result = new ArrayList<>();
        if (activity == Activity.EXPLORING) {
            for (int i = 0; i < 24; i++) {
                double radius = tuning.exploreMin + random.nextDouble()*(tuning.exploreMax-tuning.exploreMin);
                result.add(BodyMotion.offset(owner.feet(),random.nextFloat()*360,radius,0,owner.bodyHeight()+random.nextDouble()*.8));
            }
        } else {
            double distance = activity == Activity.CALLING ? tuning.callDistance
                : activity == Activity.RETREATING ? -tuning.retreatDistance : tuning.followDistance/Math.sqrt(2);
            double right = activity == Activity.FOLLOWING ? tuning.followDistance/Math.sqrt(2) : 0;
            // A nearby enemy can exclude the preferred spot; keep looking farther behind.
            for (double extra : activity == Activity.RETREATING ? new double[]{0,3,6} : new double[]{0})
                for (double dy : new double[]{0,.6,-.6})
                    for (double side : new double[]{0,1.5,-1.5,3,-3})
                        result.add(BodyMotion.offset(owner.feet(),owner.yaw(),distance-extra,right+side,owner.bodyHeight()+dy));
        }
        if (activity != Activity.CALLING) result.removeIf(p -> BodyMotion.nearFront(p,owner.feet(),owner.yaw()));
        return result;
    }
    private static Vec3d select(List<Vec3d> options, TargetSpace space, boolean walls) {
        return options.stream().filter(p -> space.clear(p,walls)).findFirst().orElse(null);
    }
}
