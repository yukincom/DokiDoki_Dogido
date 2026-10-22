package dogido.lab.control;

import java.util.Random;
import net.minecraft.util.math.MathHelper;
import net.minecraft.util.math.Vec3d;

/**
 * FOLLOWの待機中だけ、到着後の間と見回す向きをサーバーtickで決める。
 * 入力は実到着のtickと現在yaw（度）、出力は次tickのyaw。位置・視認結果・対象選択は変更しない。
 * 身体controllerが優先行動への移行時にresetし、実際の向きの反映はDogidoEntityが担当する。
 */
public final class FollowAttention {
    public static final int DWELL_TICKS = 100;
    public static final int SETTLE_TICKS = 60;
    private static final double FAST_OWNER_BLOCKS_PER_TICK = .25;
    public static final int PAUSE_MIN_TICKS = 100;
    public static final int PAUSE_MAX_TICKS = 180;
    public static final float YAW_STEP = 1.25F;
    private final Random random;
    private long dwellUntil = -1, nextLook = -1;
    private long ownerTick = -1, settleUntil = -1;
    private Vec3d ownerPosition;
    private Float targetYaw;
    private int direction = 1;

    public FollowAttention(long seed) { random = new Random(seed); }

    /** 保存しない一回の待機状態。取消後の再開で以前の見回し目標を復活させない。 */
    public void reset() {
        dwellUntil = nextLook = ownerTick = settleUntil = -1;
        targetYaw = null; ownerPosition = null;
    }

    /** 実際に接近先へ到着した時だけ呼ぶ。20tick/秒で5秒、今の向きを保持する。 */
    public void arrived(long tick) {
        targetYaw = null;
        dwellUntil = tick + DWELL_TICKS;
        nextLook = dwellUntil;
    }

    public boolean dwelling(long tick) { return tick < dwellUntil; }
    public boolean looking() { return targetYaw != null; }

    /**
     * 現在のowner足元を同じserver tickで読む。初回と速い移動（5block/秒超）の後は3秒近くへ注意を絞る。
     * 普通の歩行・ゆっくりした探索では期限を延ばさない。同tickの再読や浮遊する身体の上下動を速度に数えない。
     */
    public void observeOwner(long tick, Vec3d position) {
        if (tick <= ownerTick) return;
        if (ownerPosition == null || position.distanceTo(ownerPosition) > FAST_OWNER_BLOCKS_PER_TICK*(tick-ownerTick)) {
            settleUntil = tick+SETTLE_TICKS;
            targetYaw = null;
            nextLook = settleUntil;
        }
        ownerTick = tick; ownerPosition = position;
    }
    public boolean settling(long tick) { return tick < settleUntil; }

    /**
     * 待機の毎tickに一度呼ぶ。5〜9秒の間を挟み、45〜100度先へ最大1.25度/tickで向く。
     * 同じ方向の探索を続けつつ時々向きを変える。待機中のtickだけ進め、壁の向こうの候補は作らない。
     */
    public float yaw(long tick, float currentYaw) {
        if (dwelling(tick) || settling(tick)) return currentYaw;
        if (nextLook < 0) nextLook = tick + pause();
        if (targetYaw == null) {
            if (tick < nextLook) return currentYaw;
            if (random.nextInt(4) == 0) direction = -direction;
            targetYaw = currentYaw + direction*(45 + random.nextInt(56));
        }
        float delta = MathHelper.wrapDegrees(targetYaw-currentYaw);
        float next = MathHelper.wrapDegrees(currentYaw + MathHelper.clamp(delta,-YAW_STEP,YAW_STEP));
        if (Math.abs(delta) <= YAW_STEP) {
            targetYaw = null;
            nextLook = tick + pause();
        }
        return next;
    }

    private int pause() { return PAUSE_MIN_TICKS + random.nextInt(PAUSE_MAX_TICKS-PAUSE_MIN_TICKS+1); }
}
