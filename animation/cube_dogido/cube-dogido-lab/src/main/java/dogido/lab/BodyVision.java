package dogido.lab;

import dogido.lab.api.BodyProtocol.PoseObservation;
import dogido.lab.api.perception.BodyVisionGeometry;
import dogido.lab.api.perception.BodyVisibility;
import dogido.lab.api.BodyProtocol.VisionObservation;
import java.util.List;
import net.minecraft.block.ShapeContext;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.util.math.Vec3d;

/**
 * 同一snapshotの身体の眼・顔の向きからowner一人を最大3本のrayで判定する。入力はserver実測、出力は不変観測。
 * 位置・行動・履歴を変えず、未ロードや倒れている身体を「見えない」という確定結果へ変換しない。
 */
final class BodyVision {
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
        return BodyVisionGeometry.observe(tick,eye,pose.viewYaw(),pose.viewPitch(),targets,
            target -> BodyVisibility.trace(world,space::loaded,eye,target,ShapeContext.of(body),BodyVisibility.Purpose.VISION,null));
    }

    /** 未取得のowner視認も共有APIと同じ距離・視野定数で返す。 */
    static VisionObservation unavailable(long tick, String reason) {
        return BodyVisionGeometry.unavailable(tick,reason);
    }
}
