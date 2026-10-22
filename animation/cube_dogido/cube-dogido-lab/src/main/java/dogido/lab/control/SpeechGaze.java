package dogido.lab.control;

import dogido.lab.api.BodyProtocol.*;
import java.util.Objects;
import net.minecraft.util.math.Vec3d;

/**
 * 同じ所有者・身体世代の発話中注視点を20tickだけ保持する。入力はblock座標、出力は現在の注視点。
 * 向きの適用は実体、観測元の選択はadapterが所有する。世代変更時にclearし、保存しない。
 */
public final class SpeechGaze {
    public static final int VALID_TICKS = 20;
    private long sequence = -1, observed = -1;
    private Vec3d target;
    public void clear() { sequence = observed = -1; target = null; }
    /** 同じtickの終了通知は新sequenceなら受理し、古い再配送では期限を延ばさない。 */
    public boolean observe(SpeechGazeObservation value, Context context, long now, Vec3d eye) {
        if (value == null || context == null || !Objects.equals(context,value.context())
                || value.sequence() <= sequence || value.observedServerTick() < observed
                || value.observedServerTick() < 0 || value.observedServerTick() > now
                || now-value.observedServerTick() >= VALID_TICKS) return false;
        Position p = value.target();
        Vec3d point = p == null ? null : new Vec3d(p.x(),p.y(),p.z());
        if (point != null && (!Double.isFinite(p.x()) || !Double.isFinite(p.y()) || !Double.isFinite(p.z())
                || point.squaredDistanceTo(eye) > 64*64)) return false;
        sequence=value.sequence(); observed=value.observedServerTick(); target=point;
        return true;
    }
    public Vec3d target(long now) { return now >= observed && now-observed < VALID_TICKS ? target : null; }
}
