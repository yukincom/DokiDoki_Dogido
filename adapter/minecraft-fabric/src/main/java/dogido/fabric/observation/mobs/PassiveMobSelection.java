package dogido.fabric.observation.mobs;

import java.util.List;
import java.util.function.Predicate;

/** Keep routine observations unchanged; retain extra companions only for explicit conversation. */
public final class PassiveMobSelection {
    private static final int ROUTINE_LIMIT = 4;
    private static final int COMPANION_LIMIT = 32;

    private PassiveMobSelection() {}

    public static <T> List<T> regular(List<T> observed) {
        return List.copyOf(observed.subList(0, Math.min(observed.size(), ROUTINE_LIMIT)));
    }

    public static <T> List<T> companions(List<T> observed, Predicate<T> companion) {
        return observed.stream().skip(ROUTINE_LIMIT).filter(companion).limit(COMPANION_LIMIT).toList();
    }
}
