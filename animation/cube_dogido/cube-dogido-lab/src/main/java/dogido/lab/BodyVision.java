package dogido.lab;

import dogido.lab.api.BodyProtocol.PoseObservation;
import dogido.lab.api.BodyProtocol.Visibility;
import dogido.lab.api.BodyProtocol.VisionObservation;
import java.util.List;
import java.util.function.Function;
import net.minecraft.block.ShapeContext;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.util.math.Vec3d;

/**
 * 同一snapshotの身体の眼・顔の向きからowner一人を最大3本のrayで判定する。入力はserver実測、出力は不変観測。
 * 位置・行動・履歴を変えず、未ロードや倒れている身体を「見えない」という確定結果へ変換しない。
 */
final class BodyVision {
    static final double RANGE = 16, HORIZONTAL_FOV = 120, VERTICAL_FOV = 90;
    private static final double EPSILON = 1e-9;
    private BodyVision() {}

    /** 呼出元がserver threadと同時刻を所有する。現モデルの視線はBodyPoseObservationの顔の実測に合わせる。 */
    static VisionObservation capture(DogidoEntity body, ServerPlayerEntity owner, PoseObservation pose, long tick) {
        if (body.downed()) return unavailable(tick,"body_downed");
        if (owner == null || body.getEntityWorld() != owner.getEntityWorld()) return unavailable(tick,"owner_unavailable");
        if (pose == null) return unavailable(tick,"invalid_pose");
        var p = pose.eyePosition();
        Vec3d eye = new Vec3d(p.x(),p.y(),p.z());
        var box = owner.getBoundingBox();
        double x = (box.minX+box.maxX)/2, z = (box.minZ+box.maxZ)/2, height = box.getLengthY();
        List<Vec3d> targets = List.of(new Vec3d(x,box.minY+height*.25,z),
            new Vec3d(x,box.minY+height*.55,z),new Vec3d(x,box.minY+height*.85,z));
        ServerWorld world = (ServerWorld)body.getEntityWorld();
        BodySpace space = new BodySpace(world,body);
        return observe(tick,eye,pose.viewYaw(),pose.viewPitch(),targets,
            target -> BodyVisibility.trace(world,space::loaded,eye,target,ShapeContext.of(body),BodyVisibility.Purpose.VISION,null));
    }

    /** 範囲・視野を満たした同じ対象点へrayを引く。別の点のFOVと見通しを組み合わせて可視にしない。 */
    static VisionObservation observe(long tick, Vec3d eye, double yaw, double pitch,
            List<Vec3d> targets, Function<Vec3d,BodyVisibility.Trace> trace) {
        if (!finite(eye) || !Double.isFinite(yaw) || !Double.isFinite(pitch) || Math.abs(pitch) > 90
                || targets.isEmpty() || targets.size() > 3 || targets.stream().anyMatch(p -> !finite(p)))
            return unavailable(tick,"invalid_pose");
        boolean inRange = false, inFov = false, unknown = false;
        for (Vec3d target : targets) {
            Vec3d delta = target.subtract(eye);
            if (delta.lengthSquared() > RANGE*RANGE+EPSILON) continue;
            inRange = true;
            if (!insideFov(delta,yaw,pitch)) continue;
            inFov = true;
            var result = trace.apply(target);
            if (result == BodyVisibility.Trace.CLEAR) return result(tick,Visibility.VISIBLE,"in_view");
            if (result == BodyVisibility.Trace.UNAVAILABLE) unknown = true;
        }
        if (!inRange) return result(tick,Visibility.NOT_VISIBLE,"out_of_range");
        if (!inFov) return result(tick,Visibility.NOT_VISIBLE,"outside_fov");
        return unknown ? unavailable(tick,"area_unloaded") : result(tick,Visibility.NOT_VISIBLE,"occluded");
    }

    /** yaw=南0/西90、pitchの正は下。左右は水平全角、上下は垂直全角の半分と照合する。 */
    private static boolean insideFov(Vec3d delta, double yaw, double pitch) {
        double horizontal = delta.horizontalLength();
        if (delta.lengthSquared() < EPSILON) return true;
        double targetYaw = horizontal < EPSILON ? yaw : Math.toDegrees(Math.atan2(-delta.x,delta.z));
        double yawDifference = ((targetYaw-yaw)%360+540)%360-180;
        double targetPitch = -Math.toDegrees(Math.atan2(delta.y,horizontal));
        return Math.abs(yawDifference) <= HORIZONTAL_FOV/2+EPSILON
            && Math.abs(targetPitch-pitch) <= VERTICAL_FOV/2+EPSILON;
    }
    private static boolean finite(Vec3d p) {
        return p != null && Double.isFinite(p.x) && Double.isFinite(p.y) && Double.isFinite(p.z);
    }
    static VisionObservation unavailable(long tick, String reason) { return result(tick,Visibility.UNAVAILABLE,reason); }
    private static VisionObservation result(long tick, Visibility visibility, String reason) {
        return new VisionObservation(tick,RANGE,HORIZONTAL_FOV,VERTICAL_FOV,visibility,reason);
    }
}
