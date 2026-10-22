package dogido.lab.control;

import dogido.lab.api.BodyProtocol.Activity;
import dogido.lab.api.BodyProtocol.Mode;
import dogido.lab.IdleMotion;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;

/**
 * DIRECTの待機・休息中にブロックへ埋まった身体の、局所的な脱出先だけを保持する。
 * 入力は現在位置・既存の壁内判定・移動可能範囲の検査。出力は通常移動へ渡す目標で、
 * ワールド参照と移動は呼出し側が所有する。行動やownerの世代が変わるとclearで破棄する。
 */
public final class IdleRecovery {
    public static final double SEARCH_RADIUS = 3;
    // 任意の位相で待機へ入るため、上下波形の全振幅と到着直後の小さな慣性に余白を残す。
    private static final double HOVER_CLEARANCE = 2*IdleMotion.HOVER_AMPLITUDE+.03, ARRIVAL_DISTANCE = .04;
    private static final int RETRY_TICKS = 20;
    private static final List<Vec3d> OFFSETS = offsets();
    private Vec3d destination, origin;
    private long nextSearchTick = Long.MIN_VALUE;

    /** 再ロード後の未指示待機も対象にするが、外部操作の許可状態自体は変えない。 */
    public static boolean controlAllowsRecovery(ControlLease control, boolean localControl) {
        boolean initialIdle = !localControl && control.revision() < 0 && !control.expired();
        return control.mode() == Mode.DIRECT && !control.retreat() && (control.enabled() || initialIdle);
    }
    public static boolean eligible(ControlLease control, boolean localControl, Activity activity) {
        return controlAllowsRecovery(control,localControl) && (activity == Activity.HOLDING || activity == Activity.RESTING);
    }

    /** tick単位で更新する。平常時は検査せず、埋まり時だけ半径3block・最大156候補を調べる。 */
    public void update(Vec3d position, boolean eligible, boolean insideSolid, long tick, Predicate<Box> clear) {
        if (!eligible) { clear(); return; }
        if (target(position) != null) {
            if (!clear.test(clearanceBox(destination))) {
                destination = origin = null; nextSearchTick = tick + RETRY_TICKS;
            } else if (position.distanceTo(destination) <= ARRIVAL_DISTANCE && clear.test(clearanceBox(position))) {
                clear();
            }
            return;
        }
        if (!insideSolid || tick < nextSearchTick) return;
        nextSearchTick = tick + RETRY_TICKS;
        for (Vec3d offset : OFFSETS) {
            Vec3d candidate = position.add(offset);
            if (clear.test(clearanceBox(candidate))) { origin = position; destination = candidate; return; }
        }
    }

    /** 既存のsteerへ渡す目標。外部転送などで探索元から離れた場合は古い目標を返さない。 */
    public Vec3d target(Vec3d position) {
        if (origin != null && position.distanceTo(origin) > SEARCH_RADIUS + BodyTuning.BODY_SIZE) clear();
        return destination;
    }
    public void clear() { destination = origin = null; nextSearchTick = Long.MIN_VALUE; }
    private static Box clearanceBox(Vec3d position) { return BodyMotion.box(position).expand(0,HOVER_CLEARANCE,0); }

    private static List<Vec3d> offsets() {
        List<Vec3d> result = new ArrayList<>();
        // 距離が同じなら上方を先に検査する。目標決定後は身体が壁を抜けても到着まで保持する。
        for (double radius = .5; radius <= SEARCH_RADIUS; radius += .5)
            for (int y : new int[]{1,0,-1}) for (int x : new int[]{0,1,-1}) for (int z : new int[]{0,1,-1}) {
                if (x == 0 && y == 0 && z == 0) continue;
                result.add(new Vec3d(x,y,z).normalize().multiply(radius));
            }
        return List.copyOf(result);
    }
}
