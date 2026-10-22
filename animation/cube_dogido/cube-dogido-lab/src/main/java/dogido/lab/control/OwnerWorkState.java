package dogido.lab.control;

import dogido.lab.api.BodyProtocol.*;
import java.util.Objects;

/** adapterの既存採掘中観測を短期間だけ保持する。世代・送信元・観測tickを照合し、古い情報を立ち位置の許可に使わない。 */
public final class OwnerWorkState {
    public static final int VALID_TICKS = 20;
    private long observed = -1;
    private boolean working;

    public void clear() { observed = -1; working = false; }
    /** 同じ観測tickの再送では期限を伸ばさない。身体のcontext変更時には所有者がclearを呼ぶ。 */
    public boolean observe(OwnerWorkObservation value, Context context, long now) {
        if (value == null || value.working() == null || context == null || !Objects.equals(context,value.context())
                || value.observedServerTick() < 0 || value.observedServerTick() > now
                || now-value.observedServerTick() > VALID_TICKS || value.observedServerTick() <= observed) return false;
        observed = value.observedServerTick(); working = value.working(); return true;
    }
    /** 未観測・期限切れは正面を空ける。これは作業を観測したという意味ではなく、身体の配置だけに使う。 */
    public boolean avoidFront(long now) { return observed < 0 || now < observed || now-observed > VALID_TICKS || working; }
}
