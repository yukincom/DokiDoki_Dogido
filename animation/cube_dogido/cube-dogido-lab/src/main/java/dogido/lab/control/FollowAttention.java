package dogido.lab.control;

import net.minecraft.util.math.Vec3d;

/**
 * FOLLOWの実到着と所有者の速い移動から、次の観察移動までの待機期限だけを決める。
 * 入力はserver tickと所有者の足元座標（block）。向きの目標は作らず、待機中の実角度は実体が保つ。
 * 身体controllerが優先行動への移行時にresetし、保存やモデル判断は行わない。
 */
public final class FollowAttention {
    public static final int DWELL_TICKS = 100;
    public static final int SETTLE_TICKS = 60;
    private static final double FAST_OWNER_BLOCKS_PER_TICK = .25;
    private long dwellUntil = -1, ownerTick = -1, settleUntil = -1;
    private Vec3d ownerPosition;

    public void reset() {
        dwellUntil = ownerTick = settleUntil = -1;
        ownerPosition = null;
    }
    /** 実到着時から20tick/秒で5秒待つ。行動の選択開始時刻から数えない。 */
    public void arrived(long tick) { dwellUntil = tick + DWELL_TICKS; }
    public boolean dwelling(long tick) { return tick < dwellUntil; }

    /** 初回と5block/秒超の移動後は3秒待つ。同tickの再読と通常歩行では期限を延ばさない。 */
    public void observeOwner(long tick, Vec3d position) {
        if (tick <= ownerTick) return;
        if (ownerPosition == null || position.distanceTo(ownerPosition) > FAST_OWNER_BLOCKS_PER_TICK*(tick-ownerTick))
            settleUntil = tick+SETTLE_TICKS;
        ownerTick = tick; ownerPosition = position;
    }
    public boolean settling(long tick) { return tick < settleUntil; }
}
