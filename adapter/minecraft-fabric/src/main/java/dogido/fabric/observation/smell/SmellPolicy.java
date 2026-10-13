package dogido.fabric.observation.smell;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * 匂いの候補を集めた後、鼻に届く種類と強さをコードだけで決める純粋規則。
 * 処理順は「天候等の抑止 → 温度・距離補正 → 同種の代表選択 → 異種のマスキング」。
 * 残った種類は混合していても個別に返す。発話要否や戦闘状態はここでは決めない。
 * 数値・対象の正本は docs/smell-policy.md。Rustへ送る形は smell_policy_v2。
 */
public final class SmellPolicy {
    static final int MAX_SCAN_DISTANCE = 12;

    private SmellPolicy() {
    }

    // ゾンビ系の実体を同じ匂いIDへ写す。匂いだけでハスク等の姿を特定したことにはしない。
    // typeの適格性は専用規則、現在地で実際に届くかは後段のresolveが判断する。
    static Candidate zombieCandidate(String type, String entityId, double distance) {
        if (!ZombieScentPolicy.isCandidate(type, distance)) return null;
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

    // 手元の匂いは距離0。選択スロットやスタック数で強さを増やさない。
    // 同じ匂いの品を複数持っていても、resolveの同種集約で一種類へ戻る。
    static Candidate hotbarCandidate(String rawItemId) {
        return hotbarCandidate(rawItemId,0.0);
    }
    /** 持ち物はownerの位置にある匂い源。身体観測では受け手までの実距離を渡す。 */
    static Candidate hotbarCandidate(String rawItemId, double receiverDistance) {
        if (!Double.isFinite(receiverDistance) || receiverDistance < 0) return null;
        Spec spec = itemSpec(rawItemId);
        if (spec == null || !spec.hotbarEligible()) {
            return null;
        }
        return spec.toCandidate("hotbar", null, receiverDistance, false);
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
        // 中身のあるコンポスターは基礎3。花1との差は2なので通常の共存規則で両方残る。
        // 花だけを保護する特例は作らず、空のコンポスターは候補そのものにしない。
        if ("composter".equals(blockId)) {
            return composterFilled
                ? new Spec("composter", "compost", "unpleasant", 3, false, false, false)
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

    // 呼出側が「点火中の焚き火で実際に調理中」と確認したスロットだけを渡す。
    // hotbarの焼肉はまだ温かいとは限らないので、このheated扱いには入れない。
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

    // 全ブロックを毎回走査しないための収集半径。ここは勝敗や発話の判定ではない。
    // 基礎5の植物・醸造台と、冷気の減衰を受けない調理中の基礎3を取りこぼさない範囲にする。
    static int blockScanDistance(int temperatureModifier, boolean rainAfterActive) {
        int boundedTemperatureModifier = Math.max(-7, Math.min(2, temperatureModifier));
        int plantDistance = Math.max(
            1,
            5 + boundedTemperatureModifier + (rainAfterActive ? 1 : 0)
        );
        int cookingDistance = 3 + Math.max(0, boundedTemperatureModifier);
        return Math.min(MAX_SCAN_DISTANCE, Math.max(plantDistance, cookingDistance));
    }

    /**
     * 現在の観測だけから、一回分の嗅覚を解決する。過去の匂いは持ち越さない。
     * effectiveStrengthは優勝した順位ではなく、各種類をどれほど感じるかのスメル値。
     * 1を仄か、8を強烈という目安にし、残った全成分に値を付けて返す。
     */
    static Observation resolve(
        List<Candidate> rawCandidates,
        int temperatureModifier,
        boolean rainAfterActive,
        String suppressionReason
    ) {
        int boundedTemperatureModifier = Math.max(-7, Math.min(2, temperatureModifier));
        // 雨・雪・雷・水中は、候補の有無とは別の「嗅げない」状態。
        // noneにすると「調べたが匂いがない」という意味に変わるため、理由付きで区別する。
        if (suppressionReason != null && !suppressionReason.isBlank()) {
            return new Observation("suppressed", List.of(), boundedTemperatureModifier,
                rainAfterActive, suppressionReason, null);
        }

        Map<String, Evaluated> strongestBySmell = new LinkedHashMap<>();
        Evaluated strongestRainAfter = null;
        for (Candidate candidate : rawCandidates == null ? List.<Candidate>of() : rawCandidates) {
            if (candidate == null || !Double.isFinite(candidate.distance()) || candidate.distance() < 0.0) {
                continue;
            }
            // 普通の匂いは雨上がり中も同じ規則で計算して保持する。
            // 一度「あっ、雨上がり」と反応した後、花・食品・ゾンビの匂いへすぐ戻るため、
            // 花をrain_afterへ置き換えて元の成分を180秒消してしまわない。
            if (!"rain_after".equals(candidate.smellId())) {
                Evaluated evaluated = evaluate(candidate, boundedTemperatureModifier, 0);
                Evaluated previous = strongestBySmell.get(candidate.smellId());
                if (evaluated != null && (previous == null || evaluated.strongerThan(previous))) {
                    strongestBySmell.put(candidate.smellId(), evaluated);
                }
            }
            // 雨上がりだけは「一度の気づき」の候補として別に残す。通常臭とは競わせない。
            // 設置・手持ちの植物と土を同じ雨上がりへまとめ、強い一源だけを使う。
            // 複数の花や土があっても足し算はしない。元の採取場所は通信契約へ保持する。
            if (rainAfterActive && candidate.rainBoostEligible()) {
                Candidate rain = new Candidate("rain_after", "rain_after", "pleasant",
                    candidate.sourceKind(), candidate.sourceId(), candidate.basePropagation(),
                    candidate.distance(), candidate.heated(), true);
                Evaluated evaluated = evaluate(rain, boundedTemperatureModifier, 1);
                if (evaluated != null
                        && (strongestRainAfter == null || evaluated.strongerThan(strongestRainAfter))) {
                    strongestRainAfter = evaluated;
                }
            }
        }
        // 異種の候補同士を比較し、どの強い匂いからも隠されない種類だけ残す。
        // 最大値だけを選ぶ処理ではないので、スープ3と花1のような共存を表現できる。
        // ID順に固定し、収集順が変わっただけで受信側が別の観測と誤認しないようにする。
        List<Evaluated> candidates = List.copyOf(strongestBySmell.values());
        List<Component> components = candidates.stream()
            .filter(candidate -> candidates.stream().noneMatch(other -> masks(other, candidate)))
            .sorted(Comparator.comparing(candidate -> candidate.candidate().smellId()))
            .map(value -> new Component(value.candidate().smellId(), value.candidate().category(),
                value.candidate().valence(), value.candidate().sourceKind(), value.score()))
            .toList();
        Component rainAfter = strongestRainAfter == null ? null
            : new Component("rain_after", "rain_after", "pleasant",
                strongestRainAfter.candidate().sourceKind(), strongestRainAfter.score());
        return new Observation(components.isEmpty() ? "none" : "present", components,
            boundedTemperatureModifier, rainAfterActive, null, rainAfter);
    }

    // 通常臭と雨上がり候補で、到達・温度・距離の計算自体は共有する。
    // 優先して一言出すために雨上がりの強度を水増しせず、実際のスメル値を返す。
    private static Evaluated evaluate(Candidate candidate, int temperatureModifier, int rainModifier) {
        // heatedだけは寒さの減衰を免除する。hotbarの調理済み食品には適用しない。
        int coldSafeModifier = candidate.heated() && temperatureModifier < 0 ? 0 : temperatureModifier;
        int effectivePropagation = Math.max(1,
            Math.min(MAX_SCAN_DISTANCE, candidate.basePropagation() + coldSafeModifier + rainModifier));
        // まず各源自身が届く必要がある。タイマーや他の匂いから未検知の源を補わない。
        if (candidate.distance() > effectivePropagation) return null;
        // 距離0〜1は減衰なし。以後は距離を切り上げた段階ごとに1ずつ弱める。
        int distanceCost = Math.max(0, (int) Math.ceil(candidate.distance()) - 1);
        int score = effectivePropagation - distanceCost;
        return score < 1 ? null : new Evaluated(candidate, score);
    }

    private static boolean masks(Evaluated stronger, Evaluated weaker) {
        if (stronger == weaker) return false;
        int difference = stronger.score() - weaker.score();
        // 閾値は「隠す側」の匂いで決まる非対称な規則。
        // ゾンビ1＋花1は同点なので両方、ゾンビ2＋花1はゾンビだけ。
        // スープ3＋ゾンビ1〜3は両方だが、ゾンビ4になるとスープを隠す。
        // その他の匂いは差3まで共存し、差4以上で弱い側を隠す。
        return difference >= ("decay".equals(stronger.candidate().category()) ? 1 : 4);
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

    // 実レジストリIDと匂いの種類を対応させる閉じた表。カタログ上の別名は受け付けない。
    // 食品の生／調理済みで快不快は変えるが、基礎値はどちらも3とする。
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
        // 通常の対象花は基礎1、ライラックだけ5。到達範囲にもこの値が使われる。
        // 「花という分類だからすべて匂う」とはせず、下の表にある実IDだけを候補にする。
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

    /** レジストリごとの固定定義。現在距離や現在の強さはCandidate以降で付ける。 */
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

    /** 収集済みの一候補。距離・IDを含むため、そのまま通信JSONにしない。 */
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
        // ブロックの種類を再走査しないフレームでも、プレイヤー移動後の距離で再評価する。
        Candidate atDistance(double currentDistance) {
            return new Candidate(smellId, category, valence, sourceKind, sourceId,
                basePropagation, currentDistance, heated, rainBoostEligible);
        }
    }

    /**
     * 再走査の間だけ保持するブロック候補。座標差は距離減衰を更新するためだけに使う。
     * 方位へ変換せず、現在位置までの距離で同じCandidateを評価し直す。
     */
    record LocatedCandidate(Candidate candidate, double x, double y, double z) {
        Candidate observedFrom(double observerX, double observerY, double observerZ) {
            double dx = x - observerX;
            double dy = y - observerY;
            double dz = z - observerZ;
            return candidate.atDistance(Math.sqrt(dx * dx + dy * dy + dz * dz));
        }
    }

    /**
     * 通常の嗅覚と、一度だけ気づく雨上がり候補。statusは通常componentsに対応する。
     * 雨上がりを出力済みかはRustの配送側が所有するため、ここで発話済み扱いにしない。
     */
    public record Observation(
        String status,
        List<Component> components,
        int temperatureModifier,
        boolean rainAfterActive,
        String suppressionReason,
        Component rainAfter
    ) {}

    /**
     * マスキング後に残った一種類。複数あっても親IDのmixedへまとめない。
     * 方位・座標・個体IDを持たず、種類と補正後の強さを返す。
     */
    public record Component(
        String smellId,
        String category,
        String valence,
        String sourceKind,
        int effectiveStrength
    ) {}

    private record Evaluated(Candidate candidate, int score) {
        // 同種内の代表選択。強度が同じなら近い源、さらに同じならID順で決め、
        // 走査順だけで同種の代表属性が変わらないようにする。
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
