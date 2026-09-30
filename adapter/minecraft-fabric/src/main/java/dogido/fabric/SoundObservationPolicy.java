package dogido.fabric;

import java.util.Map;
import java.util.Set;
import java.util.UUID;

/** Sound retention uses the current position of an already heard entity, never a new sighting. */
final class SoundObservationPolicy {
    static final Set<String> NEUTRAL_MONSTER_TYPES = Set.of(
        "enderman", "spider", "cave_spider", "drowned", "piglin", "zombified_piglin"
    );

    private SoundObservationPolicy() {}

    record Point(double x, double y, double z) {}

    static UUID entityUuid(String sourceId) {
        if (sourceId == null) return null;
        try {
            return UUID.fromString(sourceId);
        } catch (IllegalArgumentException ignored) {
            return null;
        }
    }

    static Point retainedPosition(
        UUID entityUuid,
        Point original,
        Map<UUID, Point> currentPositions,
        Point listener,
        double hearingDistance
    ) {
        // A known entity disappearing must not turn back into its old sound coordinates.
        Point source = entityUuid == null ? original : currentPositions.get(entityUuid);
        if (source == null) return null;
        double dx = source.x() - listener.x();
        double dy = source.y() - listener.y();
        double dz = source.z() - listener.z();
        return dx * dx + dy * dy + dz * dz <= hearingDistance * hearingDistance ? source : null;
    }

    static boolean matchesMobSound(String soundType, String entityType) {
        return soundType.equals(entityType)
            || ("creeper".equals(soundType) && "charged_creeper".equals(entityType))
            || ("spider".equals(soundType) && "cave_spider".equals(entityType));
    }

    static boolean unresolvedSoundIsThreat(String recognizedHostileSoundType) {
        return !NEUTRAL_MONSTER_TYPES.contains(recognizedHostileSoundType);
    }

    static long ageMillis(long nowNanos, long heardAtNanos) {
        return Math.max(0L, (nowNanos - heardAtNanos) / 1_000_000L);
    }
}
