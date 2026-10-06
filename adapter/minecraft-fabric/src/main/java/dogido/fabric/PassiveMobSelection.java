package dogido.fabric;

import java.util.ArrayList;
import java.util.List;
import java.util.function.Predicate;

/** Select up to four visible mobs without losing named individuals behind followers' priority. */
final class PassiveMobSelection {
    private static final int LIMIT = 4;

    private PassiveMobSelection() {}

    static <T> List<T> select(List<T> observed, Predicate<T> hasCustomName) {
        if (observed.size() <= LIMIT) return List.copyOf(observed);
        boolean[] selected = new boolean[observed.size()];
        int count = 0;
        for (int i = 0; i < observed.size() && count < LIMIT; i++) {
            if (hasCustomName.test(observed.get(i))) {
                selected[i] = true;
                count++;
            }
        }
        for (int i = 0; i < observed.size() && count < LIMIT; i++) {
            if (!selected[i]) {
                selected[i] = true;
                count++;
            }
        }
        // Retain encounter priority among the selected mobs; do not reorder the input.
        List<T> result = new ArrayList<>(LIMIT);
        for (int i = 0; i < observed.size(); i++) {
            if (selected[i]) result.add(observed.get(i));
        }
        return List.copyOf(result);
    }
}
