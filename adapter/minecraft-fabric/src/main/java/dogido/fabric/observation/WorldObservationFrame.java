package dogido.fabric.observation;

import com.google.gson.JsonObject;
import java.util.function.Supplier;

/** Created inside one scan cycle; never retains observations across ticks or worlds. */
public final class WorldObservationFrame {
    private final Supplier<JsonObject> capture;
    private JsonObject observation;
    private final Supplier<JsonObject> captureBlocks;
    private JsonObject blocks;

    public WorldObservationFrame(Supplier<JsonObject> capture, Supplier<JsonObject> captureBlocks) {
        this.capture = capture;
        this.captureBlocks = captureBlocks;
    }
    public JsonObject blocks() {
        if (blocks == null) blocks = captureBlocks.get();
        return blocks.deepCopy();
    }

    public JsonObject copy() {
        if (observation == null) observation = capture.get();
        return observation.deepCopy();
    }
}
