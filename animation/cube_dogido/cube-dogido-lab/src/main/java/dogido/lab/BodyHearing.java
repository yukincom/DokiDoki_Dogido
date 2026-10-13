package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import java.util.ArrayDeque;
import java.util.List;
import java.util.Objects;
import java.util.UUID;

/**
 * 個体ごとの有界な聴覚保持。server発音時の受け手座標・到達距離で採否を決め、発音tickと通番を不変で保存する。
 * 呼出元はserver threadと生存/所有関係を検証する。世界・個体の読込み、音の生成、移動や行動変更は行わない。
 */
final class BodyHearing {
    static final int MAX_SOUNDS = 8, MAX_SOUND_ID_LENGTH = 160, MAX_CATEGORY_LENGTH = 16, TTL_TICKS = 300;
    private static final double POSITION_JUMP_SQUARED = 32*32;
    private final ArrayDeque<HeardSound> sounds = new ArrayDeque<>();
    private Context context;
    private Position lastReceiver;
    private long sequence, lastTick = -1, lastDroppedTick = -1;

    /** 発音元と受け手はblock座標。到達判定済の音だけに通番を割り当て、同tickの別音も区別する。 */
    boolean hear(Context current, long tick, Position receiver, Position source, String soundId, String category,
            float volume, Float pitch, double range, UUID sourceEntityId) {
        if (!synchronize(current,tick,receiver,true) || !finite(source)
                || soundId == null || soundId.isBlank() || soundId.length() > MAX_SOUND_ID_LENGTH
                || category == null || category.isBlank() || category.length() > MAX_CATEGORY_LENGTH
                || !Float.isFinite(volume) || volume <= 0 || pitch != null && (!Float.isFinite(pitch) || pitch <= 0)
                || !Double.isFinite(range) || range <= 0 || distanceSquared(receiver,source) > range*range)
            return false;
        if (sounds.size() == MAX_SOUNDS) lastDroppedTick = sounds.removeFirst().serverTick();
        sounds.addLast(new HeardSound(++sequence,tick,source,soundId,category,volume,pitch,sourceEntityId));
        return true;
    }

    /** 取得tickだけを更新し、要素の発音tick/通番は保持する。欠損・倒れ・世代や次元変更で古い音を失効する。 */
    HearingObservation observe(Context current, long tick, Position receiver, boolean available) {
        boolean observed = synchronize(current,tick,receiver,available);
        return new HearingObservation(observed ? ObservationStatus.OBSERVED : ObservationStatus.UNAVAILABLE,
            tick,observed ? List.copyOf(sounds) : List.of(),observed && lastDroppedTick >= 0);
    }

    /** epochとは別に非連続の位置変更も検知し、移動前の音を移動先の新しい観測へ持ち込まない。 */
    private boolean synchronize(Context current, long tick, Position receiver, boolean available) {
        boolean valid = available && current != null && current.ownerId() != null && current.bodyId() != null
            && current.epoch() != null && current.dimension() != null && tick >= 0 && finite(receiver);
        if (!valid || !Objects.equals(context,current) || tick < lastTick
                || lastReceiver != null && distanceSquared(lastReceiver,receiver) > POSITION_JUMP_SQUARED)
            clear();
        context = current;
        lastTick = tick;
        lastReceiver = valid ? receiver : null;
        if (!valid) return false;
        sounds.removeIf(sound -> tick-sound.serverTick() > TTL_TICKS);
        if (lastDroppedTick >= 0 && tick-lastDroppedTick > TTL_TICKS) lastDroppedTick = -1;
        return true;
    }

    /** 新しい世代でも通番は戻さず、既に配送した音との取り違えを防ぐ。永続化はepochが担当する。 */
    void clear() {
        sounds.clear(); context = null; lastReceiver = null; lastTick = -1; lastDroppedTick = -1;
    }
    private static boolean finite(Position p) {
        return p != null && Double.isFinite(p.x()) && Double.isFinite(p.y()) && Double.isFinite(p.z());
    }
    private static double distanceSquared(Position a, Position b) {
        if (!finite(a) || !finite(b)) return Double.POSITIVE_INFINITY;
        double x=a.x()-b.x(), y=a.y()-b.y(), z=a.z()-b.z();
        return x*x+y*y+z*z;
    }
}
