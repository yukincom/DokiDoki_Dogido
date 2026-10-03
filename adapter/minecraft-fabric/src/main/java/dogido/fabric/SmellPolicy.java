package dogido.fabric;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/** 距離・温度・雨上がりを含むスメルバトルを、発話前の閉じた一件へ解決する純粋規則。 */
final class SmellPolicy {
    static final int MAX_SCAN_DISTANCE = 12;

    private SmellPolicy() {
    }

    static Candidate zombieCandidate(String type, String entityId, double distance) {
        return new Candidate(
            "zombie",
            "decay",
            "unpleasant",
            "entity",
            entityId,
            8,
            distance,
            false,
            false
        );
    }

    static Candidate hotbarCandidate(String rawItemId) {
        Spec spec = itemSpec(rawItemId);
        if (spec == null || !spec.hotbarEligible()) {
            return null;
        }
        return spec.toCandidate("hotbar", null, 0.0, false);
    }

    static Candidate droppedItemCandidate(String rawItemId, String sourceId, double distance) {
        Spec spec = itemSpec(rawItemId);
        if (spec == null || !spec.droppedEligible()) {
            return null;
        }
        return spec.toCandidate("dropped_item", sourceId, distance, false);
    }

    static Candidate blockCandidate(
        String rawBlockId,
        String sourceId,
        double distance,
        boolean composterFilled,
        boolean brewingOccupied
    ) {
        String blockId = normalizeId(rawBlockId);
        if ("composter".equals(blockId)) {
            return composterFilled
                ? new Spec("composter", "compost", "unpleasant", 5, false, false, false)
                    .toCandidate("block", sourceId, distance, false)
                : null;
        }
        if ("brewing_stand".equals(blockId)) {
            return brewingOccupied
                ? new Spec("brewing_stand", "brewing", "mixed", 5, false, false, false)
                    .toCandidate("block", sourceId, distance, false)
                : null;
        }
        if ("cake".equals(blockId) || blockId.endsWith("_candle_cake")) {
            return new Spec("cake", "food", "pleasant", 3, false, false, false)
                .toCandidate("block", sourceId, distance, false);
        }
        Spec flower = flowerSpec(blockId);
        return flower == null ? null : flower.toCandidate("block", sourceId, distance, true);
    }

    static Candidate cookingCandidate(String rawItemId, String sourceId, double distance) {
        Spec item = itemSpec(rawItemId);
        if (item == null) {
            return null;
        }
        String smellId = switch (item.smellId()) {
            case "raw_meat", "cooked_meat" -> "cooking_meat";
            case "raw_fish", "cooked_fish" -> "cooking_fish";
            default -> null;
        };
        if (smellId == null) {
            return null;
        }
        return new Candidate(
            smellId,
            "food",
            "pleasant",
            "block",
            sourceId,
            3,
            distance,
            true,
            false
        );
    }

    static Candidate biomeCandidate(String rawBiomeId) {
        String biomeId = normalizeId(rawBiomeId);
        if (!"swamp".equals(biomeId) && !"mangrove_swamp".equals(biomeId)) {
            return null;
        }
        return new Candidate(
            "swamp",
            "swamp",
            "mixed",
            "biome",
            null,
            3,
            0.0,
            false,
            false
        );
    }

    static Candidate rainAfterCandidate(String sourceId, double distance) {
        return new Candidate(
            "rain_after",
            "rain_after",
            "pleasant",
            "block",
            sourceId,
            0,
            distance,
            false,
            true
        );
    }

    static int temperatureModifier(float biomeTemperature, boolean locallyCold) {
        if (!Float.isFinite(biomeTemperature)) {
            return 0;
        }
        // Minecraft値を設計上 20倍した摂氏目安へ対応させる。高所などで
        // vanillaが実際にcoldと判定した地点は、少なくとも0〜5℃帯へ寄せる。
        float effective = locallyCold ? Math.min(biomeTemperature, 0.15f) : biomeTemperature;
        if (effective >= 1.25f) {
            return 2;
        }
        if (effective >= 0.75f) {
            return 0;
        }
        if (effective > 0.25f) {
            return -3;
        }
        if (effective > -0.25f) {
            return -5;
        }
        return -7;
    }

    static int blockScanDistance(int temperatureModifier, boolean rainAfterActive) {
        int boundedTemperatureModifier = Math.max(-7, Math.min(2, temperatureModifier));
        int plantDistance = Math.max(
            1,
            5 + boundedTemperatureModifier + (rainAfterActive ? 1 : 0)
        );
        int cookingDistance = 3 + Math.max(0, boundedTemperatureModifier);
        return Math.min(MAX_SCAN_DISTANCE, Math.max(plantDistance, cookingDistance));
    }

    static Observation resolve(
        List<Candidate> rawCandidates,
        int temperatureModifier,
        boolean rainAfterActive,
        String suppressionReason
    ) {
        int boundedTemperatureModifier = Math.max(-7, Math.min(2, temperatureModifier));
        if (suppressionReason != null && !suppressionReason.isBlank()) {
            return new Observation(
                "suppressed",
                null,
                null,
                null,
                null,
                null,
                null,
                boundedTemperatureModifier,
                rainAfterActive,
                suppressionReason
            );
        }

        Map<String, Evaluated> strongestBySmell = new LinkedHashMap<>();
        for (Candidate candidate : rawCandidates == null ? List.<Candidate>of() : rawCandidates) {
            if (candidate == null || !Double.isFinite(candidate.distance()) || candidate.distance() < 0.0) {
                continue;
            }
            if ("rain_after".equals(candidate.smellId()) && !rainAfterActive) {
                continue;
            }
            // 雨上がり中の草木・土は個別の花名へ戻さず、ユーザー定義の
            // 一つの「雨上がりの匂い」として競わせる。
            if (rainAfterActive && candidate.rainBoostEligible()) {
                candidate = new Candidate(
                    "rain_after",
                    "rain_after",
                    "pleasant",
                    candidate.sourceKind(),
                    candidate.sourceId(),
                    candidate.basePropagation(),
                    candidate.distance(),
                    candidate.heated(),
                    true
                );
            }
            int coldSafeModifier = candidate.heated() && boundedTemperatureModifier < 0
                ? 0
                : boundedTemperatureModifier;
            int rainModifier = rainAfterActive && candidate.rainBoostEligible() ? 1 : 0;
            int effectivePropagation = Math.max(
                1,
                Math.min(MAX_SCAN_DISTANCE, candidate.basePropagation() + coldSafeModifier + rainModifier)
            );
            if (candidate.distance() > effectivePropagation) {
                continue;
            }
            int distanceCost = Math.max(0, (int) Math.ceil(candidate.distance()) - 1);
            int score = effectivePropagation - distanceCost;
            if (score < 1) {
                continue;
            }
            Evaluated evaluated = new Evaluated(candidate, score);
            Evaluated previous = strongestBySmell.get(candidate.smellId());
            if (previous == null || evaluated.strongerThan(previous)) {
                strongestBySmell.put(candidate.smellId(), evaluated);
            }
        }
        if (strongestBySmell.isEmpty()) {
            return new Observation(
                "none",
                null,
                null,
                null,
                null,
                null,
                null,
                boundedTemperatureModifier,
                rainAfterActive,
                null
            );
        }

        int winningScore = strongestBySmell.values().stream()
            .mapToInt(Evaluated::score)
            .max()
            .orElse(1);
        List<Evaluated> winners = strongestBySmell.values().stream()
            .filter(candidate -> candidate.score() == winningScore)
            .sorted(Comparator.comparing(candidate -> candidate.candidate().smellId()))
            .toList();
        if (winners.size() == 1) {
            Candidate winner = winners.get(0).candidate();
            return new Observation(
                "present",
                winner.smellId(),
                winner.category(),
                winner.valence(),
                winner.sourceKind(),
                "source",
                winningScore,
                boundedTemperatureModifier,
                rainAfterActive,
                null,
                resolvedSource(winners.get(0))
            );
        }

        String category = winners.get(0).candidate().category();
        boolean sameCategory = winners.stream()
            .allMatch(winner -> category.equals(winner.candidate().category()));
        if (!sameCategory) {
            return new Observation(
                "present",
                "mixed",
                "mixed",
                "mixed",
                "mixed",
                "mixed",
                winningScore,
                boundedTemperatureModifier,
                rainAfterActive,
                null
            );
        }
        String valence = mergeValence(winners);
        return new Observation(
            "present",
            category,
            category,
            valence,
            "mixed",
            "category",
            winningScore,
            boundedTemperatureModifier,
            rainAfterActive,
            null
        );
    }

    static List<Candidate> compact(List<Candidate> candidates) {
        List<Candidate> compacted = new ArrayList<>();
        if (candidates != null) {
            for (Candidate candidate : candidates) {
                if (candidate != null) {
                    compacted.add(candidate);
                }
            }
        }
        return List.copyOf(compacted);
    }

    private static String mergeValence(List<Evaluated> winners) {
        String first = winners.get(0).candidate().valence();
        return winners.stream().allMatch(winner -> first.equals(winner.candidate().valence()))
            ? first
            : "mixed";
    }

    private static Spec itemSpec(String rawItemId) {
        String itemId = normalizeId(rawItemId);
        return switch (itemId) {
            case "rotten_flesh" -> new Spec("rotten_flesh", "decay", "unpleasant", 8, true, true, false);
            case "beef", "chicken", "mutton", "porkchop", "rabbit" ->
                new Spec("raw_meat", "food", "unpleasant", 3, true, true, false);
            case "cod", "salmon", "tropical_fish", "pufferfish" ->
                new Spec("raw_fish", "food", "unpleasant", 3, true, true, false);
            case "cooked_beef", "cooked_chicken", "cooked_mutton", "cooked_porkchop", "cooked_rabbit" ->
                new Spec("cooked_meat", "food", "pleasant", 3, true, true, false);
            case "cooked_cod", "cooked_salmon" ->
                new Spec("cooked_fish", "food", "pleasant", 3, true, true, false);
            case "mushroom_stew", "rabbit_stew", "beetroot_soup", "suspicious_stew" ->
                new Spec("soup", "food", "pleasant", 3, true, true, false);
            case "cookie" -> new Spec("cookie", "food", "pleasant", 3, true, true, false);
            case "cake" -> new Spec("cake", "food", "pleasant", 3, true, true, false);
            case "bread" -> new Spec("bread", "food", "pleasant", 3, true, true, false);
            case "ink_sac" -> new Spec("ink_sac", "ink", "unpleasant", 3, true, false, false);
            default -> flowerSpec(itemId);
        };
    }

    private static Spec flowerSpec(String rawId) {
        String flowerId = normalizeId(rawId);
        if (flowerId.startsWith("potted_")) {
            flowerId = flowerId.substring("potted_".length());
        }
        if ("flowering_azalea_bush".equals(flowerId)) {
            flowerId = "flowering_azalea";
        }
        int propagation = "lilac".equals(flowerId) ? 5 : 1;
        String valence = switch (flowerId) {
            case "lily_of_the_valley", "lilac", "peony", "rose_bush", "cactus_flower", "flowering_azalea" ->
                "pleasant";
            case "wither_rose" -> "mixed";
            case "allium", "pitcher_plant", "torchflower", "open_eyeblossom" -> "unpleasant";
            default -> null;
        };
        return valence == null
            ? null
            : new Spec(flowerId, "flower", valence, propagation, true, false, true);
    }

    private static String normalizeId(String rawId) {
        String id = rawId == null ? "" : rawId.trim().toLowerCase(Locale.ROOT);
        int separator = id.indexOf(':');
        if (separator < 0) {
            return id;
        }
        if (!"minecraft".equals(id.substring(0, separator))) {
            return "";
        }
        return id.substring(separator + 1);
    }

    private static ResolvedSource resolvedSource(Evaluated winner) {
        Candidate candidate = winner.candidate();
        if (
            !List.of("block", "entity", "dropped_item").contains(candidate.sourceKind())
                || "rain_after".equals(candidate.smellId())
                || candidate.sourceId() == null || candidate.sourceId().isBlank()
        ) {
            return null;
        }
        return new ResolvedSource(candidate.sourceId(), candidate.sourceKind());
    }

    /** Private winner metadata for coarse bearing; never serialized to the server. */
    record ResolvedSource(String sourceId, String sourceKind) {
    }

    record Spec(
        String smellId,
        String category,
        String valence,
        int basePropagation,
        boolean hotbarEligible,
        boolean droppedEligible,
        boolean rainBoostEligible
    ) {
        Candidate toCandidate(String sourceKind, String sourceId, double distance, boolean forceRainBoost) {
            return new Candidate(
                smellId,
                category,
                valence,
                sourceKind,
                sourceId,
                basePropagation,
                distance,
                false,
                rainBoostEligible || forceRainBoost
            );
        }
    }

    record Candidate(
        String smellId,
        String category,
        String valence,
        String sourceKind,
        String sourceId,
        int basePropagation,
        double distance,
        boolean heated,
        boolean rainBoostEligible
    ) {
        Candidate atDistance(double currentDistance) {
            return new Candidate(smellId, category, valence, sourceKind, sourceId,
                basePropagation, currentDistance, heated, rainBoostEligible);
        }
    }

    record Observation(
        String status,
        String smellId,
        String category,
        String valence,
        String sourceKind,
        String specificity,
        Integer effectiveStrength,
        int temperatureModifier,
        boolean rainAfterActive,
        String suppressionReason,
        ResolvedSource resolvedSource
    ) {
        Observation(String status, String smellId, String category, String valence,
                    String sourceKind, String specificity, Integer effectiveStrength,
                    int temperatureModifier, boolean rainAfterActive, String suppressionReason) {
            this(status, smellId, category, valence, sourceKind, specificity, effectiveStrength,
                temperatureModifier, rainAfterActive, suppressionReason, null);
        }


    }

    private record Evaluated(Candidate candidate, int score) {
        boolean strongerThan(Evaluated other) {
            if (score != other.score) {
                return score > other.score;
            }
            if (candidate.distance() != other.candidate.distance()) {
                return candidate.distance() < other.candidate.distance();
            }
            String source = candidate.sourceId() == null ? "" : candidate.sourceId();
            String otherSource = other.candidate.sourceId() == null ? "" : other.candidate.sourceId();
            return source.compareTo(otherSource) < 0;
        }
    }
}
