package dogido.fabric.observation.smell;

import java.util.Locale;
import java.util.Set;

/** Minecraft proximity を、ゾンビ系だけの限定された匂い候補へ変える純粋規則。 */
final class ZombieScentPolicy {
    // 基礎伝播8 + 暖地補正2。最終的な到達可否はSmellPolicyが再検査する。
    static final double MAX_DISTANCE = 10.0;

    // 敵対しているかとは独立した対象表。中立状態のゾンビピグリンも同じ死臭の候補。
    // 骨だけのスケルトンなど、表にないアンデッドへ自動的には広げない。
    private static final Set<String> ELIGIBLE_TYPES = Set.of(
        "zombie",
        "zombie_villager",
        "husk",
        "drowned",
        "zombified_piglin"
    );

    private ZombieScentPolicy() {
    }

    // チキンジョッキーも騎乗中のzombie実体として同じ規則で観測する。
    // ここは収集対象と最大距離だけの検査。見えた・鳴いたことを理由に匂いを消さない。
    // 実際の寒暖による到達範囲は、この後SmellPolicy.resolveが再検査する。
    static boolean isCandidate(String rawType, double distance) {
        return ELIGIBLE_TYPES.contains(normalizeType(rawType))
            && Double.isFinite(distance)
            && distance >= 0.0
            && distance <= MAX_DISTANCE;
    }

    // minecraft:付きと省略形だけを許可し、別modの同名entityをバニラのゾンビと扱わない。
    private static String normalizeType(String rawType) {
        String type = rawType == null ? "" : rawType.trim().toLowerCase(Locale.ROOT);
        if (type.startsWith("minecraft:")) return type.substring("minecraft:".length());
        return type.contains(":") ? "" : type;
    }
}
