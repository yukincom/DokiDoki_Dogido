package dogido.lab.control;

/** 身体の速度・距離・休息・損耗・復活時間の初期値と変更可能範囲を保持する。入力は試験コマンドの数値、単位はblock/tick/HPで、個体再読み込み時に初期値へ戻る。 */
public final class BodyTuning {
    public double speed = .14, catchupSpeed = .32, retreatSpeed = .25, followDistance = 8, callDistance = 2;
    public double exploreMin = 5, exploreMax = 10, retreatDistance = 3.5, warpDistance = 24;
    // 実機調整用の初期値。自律的な休息/退避の閾値ではなく、身体に働く時間と量。
    public int wallGraceTicks = 100, wallDamageInterval = 20, restDelayTicks = 100, restHealInterval = 60, respawnDelayTicks = 100;
    public float wallDamage = 1, restHealing = 1;
    public static final double BODY_SIZE = .625, LIVING_MARGIN = .08;
    // followDistanceは見物先の通常半径。接近はモデルが選び、コードの追従はより大きい緊急境界からだけ始める。
    public static final double FOLLOW_ARRIVAL_DISTANCE = 2;
    // 崖・階段では通常の8block以内でも高さを合わせる。浮遊・一段の跳躍・狭所の上下待機では再開しない余幅。
    public static final double FOLLOW_HEIGHT_DISTANCE = dogido.lab.api.BodyProtocol.FOLLOW_HEIGHT_DISTANCE;
    public static final int TARGET_HOLD_TICKS = 60, EXPLORE_WAIT_TICKS = 100, CALL_TIMEOUT_TICKS = 400;

    /** モデル判断のため通常半径を越える余地を残す。ワープ境界より遠い追従開始値にはしない。 */
    public double emergencyFollowDistance() {
        return Math.min(warpDistance,Math.max(followDistance*1.5,followDistance+4));
    }

    public void set(String name, double value) {
        if (!Double.isFinite(value)) throw new IllegalArgumentException("finite_value_required");
        switch (name) {
            case "speed" -> { range(value, .03, Math.min(.3,catchupSpeed)); speed = value; }
            case "catchup_speed" -> { range(value, speed, .6); catchupSpeed = value; }
            case "retreat_speed" -> { range(value, .05, .4); retreatSpeed = value; }
            case "follow_distance" -> { range(value, 4, warpDistance-2); followDistance = value; }
            case "call_distance" -> { range(value, 2, 5); callDistance = value; }
            case "explore_min" -> { range(value, 5, exploreMax); exploreMin = value; }
            case "explore_max" -> { range(value, exploreMin, Math.min(20, warpDistance - 2)); exploreMax = value; }
            case "retreat_distance" -> { range(value, 2.5, 8); retreatDistance = value; }
            case "warp_distance" -> { range(value, Math.max(exploreMax,followDistance) + 2, 64); warpDistance = value; }
            case "wall_grace_ticks" -> wallGraceTicks = ticks(value,0,1200);
            case "wall_damage_interval" -> wallDamageInterval = ticks(value,1,1200);
            case "wall_damage" -> { range(value,.1,20); wallDamage = (float)value; }
            case "rest_delay_ticks" -> restDelayTicks = ticks(value,0,1200);
            case "rest_heal_interval" -> restHealInterval = ticks(value,1,1200);
            case "rest_healing" -> { range(value,.1,20); restHealing = (float)value; }
            case "respawn_delay_ticks" -> respawnDelayTicks = ticks(value,1,1200);
            default -> throw new IllegalArgumentException("unknown_parameter");
        }
    }
    private static void range(double value, double min, double max) {
        if (value < min || value > max) throw new IllegalArgumentException("range_" + min + "_" + max);
    }
    private static int ticks(double value, int min, int max) {
        range(value,min,max);
        if (value != Math.rint(value)) throw new IllegalArgumentException("integer_ticks_required");
        return (int)value;
    }
    public double speedFor(dogido.lab.api.BodyProtocol.Activity activity, double targetDistance, double ownerDistance) {
        if (activity == dogido.lab.api.BodyProtocol.Activity.RETREATING) return retreatSpeed;
        if (activity == dogido.lab.api.BodyProtocol.Activity.FOLLOWING || activity == dogido.lab.api.BodyProtocol.Activity.CALLING
                || ownerDistance > exploreMax+1)
            return Math.min(catchupSpeed,speed+Math.max(0,targetDistance-1.5)*.05);
        return speed;
    }
}
