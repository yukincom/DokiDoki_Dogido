package dogido.fabric;

import java.util.Map;
import java.util.List;
import java.util.Set;
import java.util.UUID;

/** Sound retention uses the current position of an already heard entity, never a new sighting. */
final class SoundObservationPolicy {
    static final Set<String> NEUTRAL_MONSTER_TYPES = Set.of(
        "enderman", "spider", "cave_spider", "drowned", "piglin", "zombified_piglin"
    );

    private SoundObservationPolicy() {}

    record Point(double x, double y, double z) {}

    record MobSource(UUID id, String type, Point position) {}

    /** Activation is distinct from stopping, stepping on, placing or breaking the block. */
    static String sculkActivationKind(String soundEvent) {
        if (soundEvent == null) return null;
        String id = soundEvent.startsWith("minecraft:") ? soundEvent.substring(10) : soundEvent;
        return switch (id) {
            case "block.sculk_sensor.clicking" -> "sculk_sensor";
            case "block.sculk_shrieker.shriek" -> "sculk_shrieker";
            default -> null;
        };
    }

    /** Keep the strongest recent deep-dark sound; lower sounds do not renew its age. */
    static int ominousPriority(String kind) {
        if (kind == null) return 0;
        return switch (kind) {
            case "sculk_sensor" -> 1;
            case "sculk_shrieker" -> 2;
            case "warden_heartbeat" -> 3;
            case "warden_presence" -> 4;
            case "warden_sonic_boom" -> 5;
            default -> 0;
        };
    }

    static boolean replaceOminous(String current, long currentAgeTicks, String incoming, long ttlTicks) {
        int next = ominousPriority(incoming);
        return next > 0 && (ominousPriority(current) == 0 || currentAgeTicks > ttlTicks
            || next >= ominousPriority(current));
    }

    /** Sound event IDs name the emitter: a parrot's imitation is still a parrot. */
    static String mobSoundEmitter(String soundEvent) {
        if (soundEvent == null) return null;
        String id = soundEvent.toLowerCase(java.util.Locale.ROOT);
        if (id.startsWith("minecraft:")) id = id.substring("minecraft:".length());
        if (!id.startsWith("entity.")) return null;
        int end = id.indexOf('.', "entity.".length());
        if (end < 0) return null;
        String emitter = id.substring("entity.".length(), end);
        return switch (emitter) {
            case "wolf_puglin", "wolf_sad", "wolf_angry", "wolf_grumpy", "wolf_big", "wolf_cute" -> "wolf";
            default -> emitter;
        };
    }

    /** Positional packets have no UUID. Require a unique same-species close match. */
    static UUID uniqueAmbientSource(String emitter, Point sound, List<MobSource> candidates) {
        if (emitter == null) return null;
        UUID result = null;
        for (MobSource candidate : candidates) {
            if (!matchesMobSound(emitter, candidate.type())) continue;
            Point point = candidate.position();
            double dx = sound.x() - point.x(), dy = sound.y() - point.y(), dz = sound.z() - point.z();
            // Covers packet coordinate quantization and a small client movement offset.
            if (dx * dx + dy * dy + dz * dz > 0.5 * 0.5) continue;
            if (result != null) return null;
            result = candidate.id();
        }
        return result;
    }

    static String customName(String raw) {
        if (raw == null) return null;
        String clean = raw.codePoints().filter(c -> !Character.isISOControl(c))
            .limit(64).collect(StringBuilder::new, StringBuilder::appendCodePoint, StringBuilder::append)
            .toString().strip();
        return clean.isEmpty() ? null : clean;
    }

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
