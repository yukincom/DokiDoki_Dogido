package dogido.fabric.horse_recall;

import java.util.UUID;

/** サーバーから読み直した対象の実行条件。モデルや過去snapshotの自己申告をこの値に使わない。 */
public final class HorseRecallChecks {
    private HorseRecallChecks() {}

    public record Target(boolean normalHorse, boolean alive, boolean tamed, UUID owner,
            String dimension, boolean hasPassengers, boolean hasVehicle, boolean leashed) {}

    public static String targetError(Target target, UUID player, String dimension) {
        if (target == null || !target.normalHorse()) return "horse_not_loaded";
        if (!target.alive()) return "horse_not_alive";
        if (!target.tamed() || target.owner() == null || !target.owner().equals(player)) return "horse_not_owned";
        if (!dimension.equals(target.dimension())) return "dimension_changed";
        if (target.hasPassengers() || target.hasVehicle()) return "horse_occupied";
        return target.leashed() ? "horse_leashed" : null;
    }
}
