package dogido.lab;

import dogido.lab.api.BodyProtocol.PoseObservation;
import dogido.lab.api.BodyProtocol.Position;
import net.minecraft.server.network.ServerPlayerEntity;
import net.minecraft.util.math.Vec3d;

/** 論理server threadから同じ世界の身体とownerを受け取り、実測値だけを不変レコードにする。状態・移動は変更しない。 */
final class BodyPoseObservation {
    private BodyPoseObservation() {}
    /** 座標はblock。身体の足元とyawは呼出元Snapshotが同じtickで採り、ここは眼とplayer足元を補う。 */
    static PoseObservation capture(DogidoEntity body, ServerPlayerEntity owner) {
        if (owner == null || body.getEntityWorld() != owner.getEntityWorld()) return null;
        // 撮影カメラと同じ身体のyaw/pitchを運ぶ。描画モデルの形状で上下視線を0へ置き換えない。
        return new PoseObservation(position(body.getEyePos()),position(owner.getEntityPos()),
            body.getBodyYaw(),body.getPitch(),owner.getYaw(),owner.getPitch());
    }
    private static Position position(Vec3d p) { return new Position(p.x,p.y,p.z); }
}
