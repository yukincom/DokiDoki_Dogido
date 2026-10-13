package dogido.fabric.observation.sound;

import java.util.ArrayDeque;
import java.util.Deque;
import net.minecraft.util.math.Vec3d;

/** 同じ位置付き実音のserver記録とclient再生を一対一で照合し、遅い配送で発音時刻を若返らせない。 */
final class BodySoundDeduplicator {
    private static final long MATCH_NANOS = 1_000_000_000L;
    private static final long RETAIN_NANOS = 2_000_000_000L;
    private static final int MAX_PENDING = 128;
    private final Deque<Pending> pending = new ArrayDeque<>();
    private record Pending(String event, Vec3d source, long heardAt, String sourceId, boolean server, long receivedAt) {}
    record Stamp(long heardAt, String previousSourceId, long previousHeardAt) {}

    Stamp reconcile(String event, Vec3d source, long heardAt, String sourceId, boolean server, long now) {
        pending.removeIf(item -> now-item.receivedAt() > RETAIN_NANOS);
        Pending match = null;
        long closest = Long.MAX_VALUE;
        for (Pending item : pending) {
            long difference = Math.abs(item.heardAt()-heardAt);
            // sound packet座標の1/8block量子化は許容し、別地点・同じ経路の繰り返しは別発音として残す。
            if (item.server() != server && item.event().equals(event) && item.source().squaredDistanceTo(source) <= .25*.25
                    && difference <= MATCH_NANOS && difference < closest) {
                match = item; closest = difference;
            }
        }
        if (match != null) {
            pending.remove(match);
            return new Stamp(Math.min(match.heardAt(),heardAt),match.sourceId(),match.heardAt());
        }
        pending.addLast(new Pending(event,source,heardAt,sourceId,server,now));
        while (pending.size() > MAX_PENDING) pending.removeFirst();
        return new Stamp(heardAt,null,heardAt);
    }
    void reset() { pending.clear(); }
    int pendingCount() { return pending.size(); }
}
