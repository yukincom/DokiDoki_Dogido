package dogido.fabric;

import java.util.Locale;
import java.util.Set;

/** Minecraft proximity を、ゾンビ系だけの限定された匂い手掛かりへ変える純粋規則。 */
final class ZombieScentPolicy {
    static final double MAX_DISTANCE = 8.0;

    private static final Set<String> ELIGIBLE_TYPES = Set.of(
        "zombie",
        "zombie_villager",
        "husk",
        "drowned"
    );

    private ZombieScentPolicy() {
    }

    static boolean shouldExposeClue(
        String rawType,
        double distance,
        boolean lineOfSight,
        boolean confirmedVisible,
        boolean heardFromSameEntity
    ) {
        String type = normalizeType(rawType);
        return ELIGIBLE_TYPES.contains(type)
            && Double.isFinite(distance)
            && distance >= 0.0
            && distance <= MAX_DISTANCE
            && !lineOfSight
            && !confirmedVisible
            && !heardFromSameEntity;
    }

    private static String normalizeType(String rawType) {
        String type = rawType == null ? "" : rawType.trim().toLowerCase(Locale.ROOT);
        return type.startsWith("minecraft:") ? type.substring("minecraft:".length()) : type;
    }
}
