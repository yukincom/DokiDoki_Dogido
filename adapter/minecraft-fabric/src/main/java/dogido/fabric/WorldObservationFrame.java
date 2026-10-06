package dogido.fabric;

import com.google.gson.JsonObject;
import java.util.function.Supplier;

/** Created inside one scan cycle; never retains observations across ticks or worlds. */
final class WorldObservationFrame {
    private final Supplier<JsonObject> capture;
    private JsonObject observation;

    WorldObservationFrame(Supplier<JsonObject> capture) { this.capture = capture; }

    JsonObject copy() {
        if (observation == null) observation = capture.get();
        return observation.deepCopy();
    }
}
