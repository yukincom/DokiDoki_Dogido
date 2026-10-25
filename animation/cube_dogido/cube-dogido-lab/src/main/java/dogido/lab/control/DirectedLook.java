package dogido.lab.control;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.api.perception.BodyVisionGeometry;
import java.util.UUID;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;

/**
 * 明示された一点へ最大100tickで向き、完了から100tick保持する。座標はblock、角度は度。
 * 実体が回転した後だけ一度成功を返す。移動・所有者・世代・受付鮮度は既存実行器が検査する。
 */
public final class DirectedLook {
    public static final int TURN_TIMEOUT_TICKS = 100;
    public static final int HOLD_TICKS = 100;
    public record End(UUID actionId,Outcome outcome,String reason) {}
    private UUID id;
    private Vec3d target;
    private long expires;
    private boolean completed;
    public boolean active() { return id != null; }
    public Vec3d target() { return target; }
    public void clear() { id = null; target = null; completed = false; expires = 0; }
    /** 受付済み要求を開始する。再送はControlLeaseが除外するため期限を更新しない。 */
    public void begin(UUID actionId, Position point, long now) {
        clear(); id = actionId; target = new Vec3d(point.x(),point.y(),point.z()); expires = now+TURN_TIMEOUT_TICKS;
    }
    /** 新しい移動を選ばず、待機・通常探索だけをその場で止める。追従や危険回避には譲る。 */
    public static String priority(boolean enabled, boolean retreat, boolean calling, boolean busy,
            Activity activity, boolean following, boolean danger) {
        if (!enabled) return "control_inactive";
        if (retreat || danger) return "danger_has_priority";
        if (calling) return "call_has_priority";
        if (busy) return "body_action_busy";
        if (following) return "distance_follow_has_priority";
        return activity == Activity.HOLDING || activity == Activity.EXPLORING ? null : "body_activity_busy";
    }
    /** 成功前の取消・期限切れだけ結果にする。成功後の保持終了で成功結果を書き換えない。 */
    public End stop(long now, String interruption) {
        if (!active() || interruption == null && now < expires) return null;
        End result = completed ? null : new End(id,
            interruption == null ? Outcome.EXPIRED : Outcome.INTERRUPTED,
            interruption == null ? "look_timeout" : interruption);
        clear(); return result;
    }
    /** 適用後の実yaw/pitchを照合する。別actionの完了へ流用しないようIDを返す。 */
    public UUID reached(Vec3d eye, float yaw, float pitch, long now) {
        if (!active() || completed) return null;
        var angles = BodyVisionGeometry.facing(target.subtract(eye),yaw,pitch);
        if (Math.abs(MathHelper.wrapDegrees(angles.yaw()-yaw)) > 1 || Math.abs(angles.pitch()-pitch) > 1) return null;
        // 回転にかかった時間で保持時間を削らず、実角度が一致した時から数える。
        completed = true; expires = now+HOLD_TICKS; return id;
    }
}
