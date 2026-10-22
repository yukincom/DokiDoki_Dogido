package dogido.lab.control;

import dogido.lab.api.BodyProtocol.Mode;

/** 呼び寄せ先の解決方針。所有台帳と読込み済み個体を区別し、未ロードや複数候補を新規出現で置き換えない。実生成・登録はserver側が担当する。 */
public final class BodyRecallPolicy {
    public enum Resolution { EXISTING, UNLOADED, BIND_NEARBY, AMBIGUOUS, SPAWN }
    private BodyRecallPolicy() {}
    public static Mode savedLocalMode(String value) {
        try { return Mode.valueOf(value); } catch (IllegalArgumentException e) { return Mode.FOLLOW; }
    }
    public static Resolution resolve(boolean registered, boolean loaded, int unboundNearby) {
        if (registered) return loaded ? Resolution.EXISTING : Resolution.UNLOADED;
        if (unboundNearby > 1) return Resolution.AMBIGUOUS;
        return unboundNearby == 1 ? Resolution.BIND_NEARBY : Resolution.SPAWN;
    }
    /** 同じ生存ownerの次元移動だけでローカルCALLを継続する。切断・復活・owner交代の旧要求は再実行しない。 */
    public static boolean continueAcrossDimension(boolean local, boolean sameOwner, boolean wasAvailable,
            boolean available, String before, String after) {
        return local && sameOwner && wasAvailable && available && before != null && after != null && !before.equals(after);
    }
}
