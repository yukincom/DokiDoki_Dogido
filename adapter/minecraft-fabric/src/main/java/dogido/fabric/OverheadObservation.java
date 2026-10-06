package dogido.fabric;

import java.util.function.IntFunction;

/** A single bounded upward scan. Clearance at the limit does not prove a ceiling exists. */
record OverheadObservation(int clearance, String coverType, boolean ceilingObserved) {
    static final int SEARCH_LIMIT = 24;

    static OverheadObservation scan(IntFunction<String> blockAtStep) {
        boolean sawFluid = false;
        for (int step = 1; step <= SEARCH_LIMIT; step++) {
            String material = blockAtStep.apply(step);
            if ("fluid".equals(material)) {
                sawFluid = true;
            } else if (!"air".equals(material)) {
                return new OverheadObservation(step, material, true);
            }
        }
        return new OverheadObservation(SEARCH_LIMIT, sawFluid ? "fluid" : "none", false);
    }
}
