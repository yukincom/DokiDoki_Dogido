package dogido.fabric.body;

import net.minecraft.util.math.Vec3d;

/** 身体聴覚の距離と時刻だけを計算する。音の種類・保持・発話は既存sound collectorと本体が所有する。 */
public final class BodyHearingPolicy {
    private BodyHearingPolicy() {}
    public static boolean arrives(Vec3d receiver, Vec3d source, double range) {
        return Double.isFinite(range) && range >= 0 && receiver.squaredDistanceTo(source) <= range*range;
    }
    /** serverで確認済みの発音は同じsnapshot tickから年齢を測り、受信後のageを一度だけ足す。 */
    static long serverSoundAge(long snapshotTick, long soundTick, long outerAge) {
        if (soundTick < 0 || snapshotTick < soundTick || outerAge < 0 || snapshotTick-soundTick > 300) return -1;
        return (snapshotTick-soundTick)*50L+outerAge;
    }
    /** HTTP内側はsnapshot時点に戻す。Rustが外側ageを一度だけ足して現時点の年齢へ戻す。 */
    static long referenceAge(long currentAge, long outerAge) { return Math.max(0,currentAge-outerAge); }
}
