package dogido.fabric.horse_recall;

import java.util.UUID;

/** 正本から取得した種類・所有者・乗用資格を検査する。モデルの自己申告を実行許可に使わない。 */
public final class HorseRecallChecks {
    private HorseRecallChecks() {}

    /** 1.21.11の乗れる動物。wire名はRustのMountKindと同じで、普通のハスクは含まない。 */
    public enum Kind {
        HORSE, DONKEY, MULE, SKELETON_HORSE, ZOMBIE_HORSE, CAMEL, CAMEL_HUSK,
        PIG, STRIDER, HAPPY_GHAST, NAUTILUS, ZOMBIE_NAUTILUS, LLAMA, TRADER_LLAMA;

        public String wireName() { return name().toLowerCase(java.util.Locale.ROOT); }
        public boolean aquatic() { return this == NAUTILUS || this == ZOMBIE_NAUTILUS; }
        public boolean flying() { return this == HAPPY_GHAST; }
        public boolean rears() {
            return this == HORSE || this == DONKEY || this == MULE || this == SKELETON_HORSE || this == ZOMBIE_HORSE;
        }
        public boolean requiresTaming() { return this != PIG && this != STRIDER && this != HAPPY_GHAST; }
        public boolean permitsNamedEquipment() {
            return this == SKELETON_HORSE || this == CAMEL || this == CAMEL_HUSK
                || this == PIG || this == STRIDER || this == HAPPY_GHAST;
        }
        public static Kind fromEntityId(String id) {
            if (id == null || !id.startsWith("minecraft:")) return null;
            for (Kind kind : values()) if (id.equals("minecraft:" + kind.wireName())) return kind;
            return null;
        }
    }

    /** equippedは鞍、HappyGhastだけハーネス。leashedには吊っている個体のロープも含む。 */
    public record Target(Kind kind, boolean alive, boolean tamed, UUID owner, String customName,
            boolean adult, boolean equipped, String dimension, boolean hasPassengers, boolean hasVehicle, boolean leashed) {}

    /** Ownerがある個体を名札条件で迂回しない。所有者なしの許可済み種類だけ名札＋乗用装備を認める。 */
    public static boolean authorized(Target target, UUID player) {
        if (target == null || target.kind() == null) return false;
        if (target.owner() != null) return target.kind().requiresTaming() && target.tamed() && target.owner().equals(player);
        return target.kind().permitsNamedEquipment() && target.customName() != null && !target.customName().isBlank()
            && target.equipped() && (!target.kind().requiresTaming() || target.tamed());
    }

    public static String targetError(Target target, UUID player, String dimension) {
        if (target == null || target.kind() == null) return "horse_not_loaded";
        if (!target.alive()) return "horse_not_alive";
        if (!authorized(target, player)) return "horse_not_owned";
        if (!target.adult()) return "mount_not_adult";
        if (!dimension.equals(target.dimension())) return "dimension_changed";
        if (target.hasPassengers() || target.hasVehicle()) return "horse_occupied";
        return target.leashed() ? "horse_leashed" : null;
    }
}
