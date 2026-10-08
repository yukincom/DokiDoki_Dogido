package dogido.fabric;

import com.google.gson.JsonObject;
import java.util.function.Supplier;

/** Created inside one scan cycle; never retains observations across ticks or worlds. */
final class WorldObservationFrame {
    private final Supplier<JsonObject> capture;
    private JsonObject observation;
    private final Supplier<JsonObject> captureBlocks;
    private JsonObject blocks;

    WorldObservationFrame(Supplier<JsonObject> capture, Supplier<JsonObject> captureBlocks) {
        this.capture = capture;
        this.captureBlocks = captureBlocks;
    }
    JsonObject blocks() {
        if (blocks == null) blocks = captureBlocks.get();
        return blocks.deepCopy();
    }

    JsonObject copy() {
        if (observation == null) observation = capture.get();
        return observation.deepCopy();
    }
}
