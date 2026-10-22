package dogido.lab.control;

import dogido.lab.api.BodyProtocol.*;

/**
 * 休息・壁内損耗・復活待ちの時間をサーバーtick単位で管理する。HPの正本はMinecraft Entity。
 * 入力は実際の姿勢/HP/持ち主の可用性、出力は当tickの損耗・回復量と復活準備完了。
 * 休む/逃げる/食べる判断は行わず、明示されたRESTと実際の静止だけを回復へ反映する。
 */
public final class BodyVitals {
    public record Step(float wallDamage, float recovery, boolean respawnReady) {
        static final Step NONE = new Step(0,0,false);
    }
    private int wallTicks, restTicks, respawnRemaining;
    private boolean downed;
    private long damageSequence, respawnCount;
    private DamageEvent lastDamage;

    public Step step(boolean ownerAvailable, boolean damageEnabled, boolean insideWall,
            boolean resting, float health, float maxHealth, BodyTuning rules) {
        if (!ownerAvailable) return Step.NONE;
        if (downed) {
            if (respawnRemaining > 0) respawnRemaining--;
            return new Step(0,0,respawnRemaining == 0);
        }
        wallTicks = insideWall ? Math.min(Integer.MAX_VALUE-1,wallTicks)+1 : 0;
        float damage = damageEnabled && insideWall && wallTicks > rules.wallGraceTicks
            && (wallTicks-rules.wallGraceTicks-1) % rules.wallDamageInterval == 0 ? rules.wallDamage : 0;
        restTicks = resting && health < maxHealth ? Math.min(Integer.MAX_VALUE-1,restTicks)+1 : 0;
        float heal = restTicks > rules.restDelayTicks
            && (restTicks-rules.restDelayTicks-1) % rules.restHealInterval == 0 ? rules.restHealing : 0;
        return new Step(damage,damage > 0 ? 0 : heal,false);
    }
    public void recordDamage(long tick, float amount, String cause) {
        if (!Float.isFinite(amount) || amount <= 0) return;
        restTicks = 0;
        lastDamage = new DamageEvent(++damageSequence,tick,amount,cause);
    }
    public void collapse(BodyTuning rules) {
        if (downed) return;
        downed = true; respawnRemaining = rules.respawnDelayTicks; restTicks = 0;
    }
    /** 復活配置に成功した後だけ呼ぶ。失敗時はDOWNEDを保持して次tickで再確認する。 */
    public void revived() {
        downed = false; respawnRemaining = wallTicks = restTicks = 0; respawnCount++;
    }
    public void restore(boolean wasDowned, int wall, int remaining, long respawns, long damageSerial) {
        downed = wasDowned; wallTicks = Math.max(0,wall); respawnRemaining = Math.max(0,remaining);
        respawnCount = Math.max(0,respawns); damageSequence = Math.max(0,damageSerial);
        restTicks = 0; lastDamage = null;
    }
    public Vitals snapshot(float health, float maximum, boolean damageEnabled, String gameMode) {
        return new Vitals(downed ? Life.DOWNED : Life.ACTIVE,health,maximum,damageEnabled,gameMode,
            wallTicks,restTicks,respawnRemaining,respawnCount,lastDamage);
    }
    /** 被弾直後を落ち着いた状態と扱わないための実測時刻。HP量から安心度は計算しない。 */
    public boolean recentlyDamaged(long tick, int withinTicks) {
        return lastDamage != null && tick >= lastDamage.serverTick() && tick-lastDamage.serverTick() <= withinTicks;
    }
    public boolean downed() { return downed; }
    public int wallTicks() { return wallTicks; }
    public int remaining() { return respawnRemaining; }
    public long respawnCount() { return respawnCount; }
    public long damageSequence() { return damageSequence; }
    public static float foodRecovery(int nutrition, float health, float maximum, boolean downed) {
        return downed || nutrition <= 0 || !Float.isFinite(health) || !Float.isFinite(maximum)
            ? 0 : Math.max(0,Math.min(nutrition,maximum-health));
    }
}
