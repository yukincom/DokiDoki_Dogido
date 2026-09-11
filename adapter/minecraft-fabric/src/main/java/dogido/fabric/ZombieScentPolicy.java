package dogido.fabric;

import java.util.Locale;
import java.util.Set;

/** Minecraft proximity を、ゾンビ系だけの限定された匂い候補へ変える純粋規則。 */
final class ZombieScentPolicy {
    // 基礎伝播8 + 暖地補正2。最終的な到達可否はSmellPolicyが再検査する。
    static final double MAX_DISTANCE = 10.0;

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
