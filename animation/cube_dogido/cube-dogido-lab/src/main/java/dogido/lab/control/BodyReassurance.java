package dogido.lab.control;

import dogido.lab.api.BodyProtocol.Reassurance;
import net.minecraft.util.math.Vec3d;

/**
 * 保存しない安心度を、連続したserver tickの距離・実視認・静止から更新する。HPや命令受付には依存しない。
 * 値は0..100。近くでownerが見えると70まで、落ち着いて顔が見えると100まで回復する。
 * 状態の所有者は身体Entity。読取・再送・未観測の時間から回復を作らない。
 */
public final class BodyReassurance {
    public static final double INITIAL = 50, NEAR_CAP = 70, NEAR_DISTANCE = 3, FACE_DISTANCE = 2.5;
    private static final int UNSEEN_GRACE_TICKS = 40;
    private double value = INITIAL;
    private long lastTick = -1;
    private int unseenTicks;
    private Vec3d lastBody, lastOwner;

    /** 再読込・owner/次元/世代の変更で初期化する。前の相手の安心度を別の接続へ持ち越さない。 */
    public void reset() { value=INITIAL; lastTick=-1; unseenTicks=0; lastBody=lastOwner=null; }

    /**
     * 位置はblock。safeは当tickの敵・壁・退避状態の検査結果。姿勢の連続実測があるtickだけ時間を進める。
     * 同tick/古いtickは無視し、長い未観測区間を現在の顔の見通しだけで回復時間へ換算しない。
     */
    public void step(long tick, Vec3d body, Vec3d owner, boolean ownerVisible, boolean faceVisible, boolean ownerWorking, boolean safe) {
        if (tick < 0 || tick <= lastTick || !finite(body)) return;
        boolean available=finite(owner), visible=available && ownerVisible;
        double distance=available ? body.distanceTo(owner) : 0;
        if (lastTick >= 0 && tick == lastTick+1) {
            unseenTicks=visible ? 0 : Math.min(UNSEEN_GRACE_TICKS+1,unseenTicks+1);
            boolean calm=available && lastOwner != null && lastBody != null
                && owner.distanceTo(lastOwner) <= .03 && body.distanceTo(lastBody) <= .05;
            if (safe && visible && distance <= FACE_DISTANCE && faceVisible && !ownerWorking && calm) {
                value=Math.min(100,value+.2);
            } else if (safe && visible && distance <= NEAR_DISTANCE) {
                if (value < NEAR_CAP) value=Math.min(NEAR_CAP,value+.1);
            } else {
                double loss=available && distance > NEAR_DISTANCE ? Math.min(.06,(distance-NEAR_DISTANCE)*.005) : 0;
                if (unseenTicks > UNSEEN_GRACE_TICKS) loss+=.04;
                if (!safe) loss+=.025;
                value=Math.max(0,value-loss);
            }
        } else unseenTicks=0;
        lastTick=tick; lastBody=body; lastOwner=available ? owner : null;
    }

    /** 現在の実測だけを添えた読み取り。呼び出しても値・時計・回復量を変えない。 */
    public Reassurance reading(double ownerDistance, boolean ownerVisible, boolean faceVisible, boolean ownerWorking) {
        double distance=Double.isFinite(ownerDistance) && ownerDistance >= 0 ? ownerDistance : 0;
        return new Reassurance(value,distance,ownerVisible,ownerVisible && faceVisible,ownerWorking);
    }
    public double value() { return value; }
    private static boolean finite(Vec3d point) {
        return point != null && Double.isFinite(point.x) && Double.isFinite(point.y) && Double.isFinite(point.z);
    }
}
