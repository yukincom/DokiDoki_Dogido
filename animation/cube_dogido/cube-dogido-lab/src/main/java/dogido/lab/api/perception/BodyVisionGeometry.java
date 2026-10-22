package dogido.lab.api.perception;

import dogido.lab.api.BodyProtocol.Visibility;
import dogido.lab.api.BodyProtocol.VisionObservation;
import java.util.List;
import java.util.function.Function;
import net.minecraft.util.math.Vec3d;

/**
 * 身体とadapterで共有する視野幾何。実測した眼・角度・対象点から範囲/FOVを照合し、同じ点のray結果を三値で返す。
 * 世界読込みと観測時刻は呼出元が所有する。owner視認の既定値を保ち、周辺collectorも同じ計算法を使う。
 */
public final class BodyVisionGeometry {
    public static final double RANGE = 16, HORIZONTAL_FOV = 120, VERTICAL_FOV = 90;
    private static final double EPSILON = 1e-9;
    private BodyVisionGeometry() {}

    /** 既存owner視認の範囲と全角FOV。座標はblock、yaw/pitchは度、pitchの正は下。 */
    public static VisionObservation observe(long tick, Vec3d eye, double yaw, double pitch,
            List<Vec3d> targets, Function<Vec3d,BodyVisibility.Trace> trace) {
        return observe(tick,eye,yaw,pitch,RANGE,HORIZONTAL_FOV,VERTICAL_FOV,targets,trace);
    }

    /** 最大3点のうち範囲・FOV・見通しが同時に成立する点だけを視認とする。範囲外ではworldを読まない。 */
    public static VisionObservation observe(long tick, Vec3d eye, double yaw, double pitch,
            double range, double horizontalFov, double verticalFov,
            List<Vec3d> targets, Function<Vec3d,BodyVisibility.Trace> trace) {
        if (!finite(eye) || !Double.isFinite(yaw) || !Double.isFinite(pitch) || Math.abs(pitch) > 90
                || !Double.isFinite(range) || range <= 0 || !Double.isFinite(horizontalFov)
                || horizontalFov <= 0 || horizontalFov > 360 || !Double.isFinite(verticalFov)
                || verticalFov <= 0 || verticalFov > 180 || targets == null || targets.isEmpty()
                || targets.size() > 3 || targets.stream().anyMatch(p -> !finite(p)) || trace == null)
            return unavailable(tick,"invalid_pose");
        boolean inRange = false, inFov = false, unknown = false;
        for (Vec3d target : targets) {
            Vec3d delta = target.subtract(eye);
            if (delta.lengthSquared() > range*range+EPSILON) continue;
            inRange = true;
            if (!insideFov(delta,yaw,pitch,horizontalFov,verticalFov)) continue;
            inFov = true;
            var result = trace.apply(target);
            if (result == BodyVisibility.Trace.CLEAR)
                return result(tick,range,horizontalFov,verticalFov,Visibility.VISIBLE,"in_view");
            if (result == BodyVisibility.Trace.UNAVAILABLE) unknown = true;
        }
        String reason = !inRange ? "out_of_range" : !inFov ? "outside_fov" : unknown ? "area_unloaded" : "occluded";
        return result(tick,range,horizontalFov,verticalFov,
            unknown && inFov ? Visibility.UNAVAILABLE : Visibility.NOT_VISIBLE,reason);
    }

    /**
     * 顔の確認は双方の顔向きと、身体の眼からownerの眼への実rayが揃った場合だけ成立する。
     * ownerの背後・視野外・遮蔽・未取得を、単なる近さから顔の確認へ置換しない。
     */
    public static boolean ownerFaceVisible(long tick, Vec3d bodyEye, double bodyYaw, double bodyPitch,
            Vec3d ownerEye, double ownerYaw, double ownerPitch, Function<Vec3d,BodyVisibility.Trace> trace) {
        if (!finite(bodyEye) || !finite(ownerEye) || trace == null) return false;
        if (observe(tick,ownerEye,ownerYaw,ownerPitch,List.of(bodyEye),p -> BodyVisibility.Trace.CLEAR)
                .playerVisibility() != Visibility.VISIBLE) return false;
        return observe(tick,bodyEye,bodyYaw,bodyPitch,List.of(ownerEye),trace).playerVisibility() == Visibility.VISIBLE;
    }

    /** yaw=南0/西90。角度を一周で正規化し、水平・垂直それぞれ全角の半分へ照合する。 */
    private static boolean insideFov(Vec3d delta, double yaw, double pitch, double horizontalFov, double verticalFov) {
        if (delta.lengthSquared() < EPSILON) return true;
        Angles target = facing(delta,yaw,pitch);
        double yawDifference = ((target.yaw()-yaw)%360+540)%360-180;
        return Math.abs(yawDifference) <= horizontalFov/2+EPSILON
            && Math.abs(target.pitch()-pitch) <= verticalFov/2+EPSILON;
    }
    public record Angles(double yaw, double pitch) {}
    /** owner視認と発話注視で共有する実座標差からの角度。真上/真下はyaw、同一点は両角度を維持する。 */
    public static Angles facing(Vec3d delta, double currentYaw, double currentPitch) {
        if (!finite(delta) || delta.lengthSquared() < EPSILON) return new Angles(currentYaw,currentPitch);
        double horizontal = delta.horizontalLength();
        return new Angles(horizontal < EPSILON ? currentYaw : Math.toDegrees(Math.atan2(-delta.x,delta.z)),
            -Math.toDegrees(Math.atan2(delta.y,horizontal)));
    }
    private static boolean finite(Vec3d p) {
        return p != null && Double.isFinite(p.x) && Double.isFinite(p.y) && Double.isFinite(p.z);
    }
    public static VisionObservation unavailable(long tick, String reason) {
        return result(tick,RANGE,HORIZONTAL_FOV,VERTICAL_FOV,Visibility.UNAVAILABLE,reason);
    }
    private static VisionObservation result(long tick, double range, double horizontalFov, double verticalFov,
            Visibility visibility, String reason) {
        return new VisionObservation(tick,range,horizontalFov,verticalFov,visibility,reason);
    }
}
