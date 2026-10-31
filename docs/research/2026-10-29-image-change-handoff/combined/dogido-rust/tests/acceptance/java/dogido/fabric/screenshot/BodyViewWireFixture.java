/** 合成画素を実比較・画像符号化・通信契約へ通し、Rust受入試験へJSON eventを返す。GPU描画とHTTP配送は行わない。 */
package dogido.fabric.screenshot;

import com.google.gson.*;
import java.awt.image.BufferedImage;
import java.io.InputStreamReader;
import java.time.Instant;

public final class BodyViewWireFixture {
    private static BufferedImage image(int color) {
        var image = new BufferedImage(64, 36, BufferedImage.TYPE_INT_RGB);
        for (int y = 0; y < 36; y++) for (int x = 0; x < 64; x++) image.setRGB(x, y, color);
        return image;
    }
    public static void main(String[] args) throws Exception {
        var input = JsonParser.parseReader(new InputStreamReader(System.in)).getAsJsonObject();
        var commands = BodyViewProtocol.parseCommands(input);
        if (commands.size() != 1) throw new AssertionError("exactly one real capture command required");
        var command = commands.get(0);
        var changes = new BodyViewChange();
        var scenario = input.get("scenario").getAsString();
        if (!scenario.equals("unknown")) for (int i = 0; i < 4; i++) changes.observe("body", 1, image(0x808080));
        var pixels = image(scenario.equals("changed") ? 0xffffff : 0x808080);
        var change = changes.observe("body", 1, pixels);
        var metadata = new JsonObject();
        metadata.addProperty("annotation_status", "owner_not_in_render_list");
        var image = BodyViewImage.encode(pixels, metadata);
        var now = Instant.parse(input.get("captured_at").getAsString());
        var view = new JsonObject();
        view.addProperty("png_base64", image.base64()); view.addProperty("sha256", image.sha256());
        view.addProperty("width", image.width()); view.addProperty("height", image.height());
        view.addProperty("captured_at", now.toString()); view.addProperty("capture_id", command.id());
        view.addProperty("body_entity_uuid", command.bodyId().toString()); view.addProperty("body_epoch", command.epoch().toString());
        view.addProperty("owner_uuid", command.ownerId().toString()); view.addProperty("dimension", command.dimension());
        view.addProperty("camera_restored", true); view.add("annotation_status", metadata.get("annotation_status"));
        view.addProperty("motion_sequence", command.afterMotionSequence() + 1);
        view.addProperty("visual_change", change.changed()); view.addProperty("compared_to_four_before", change.compared());
        view.addProperty("pixel_mean_difference", change.meanDifference()); view.addProperty("pixel_changed_fraction", change.changedFraction());
        var result = BodyViewProtocol.result(command, "succeeded", "captured", now);
        result.add("body_view", view);
        var state = new BodyViewProtocol.State();
        if (!state.claim(command.id())) throw new AssertionError("new command");
        state.remember(result);
        var event = input.getAsJsonObject("event");
        event.add("command_results", BodyViewProtocol.resultBatch(new JsonArray(), state.results()));
        System.out.println(BodyViewProtocol.encodeEvent(event, new Gson(), rejected -> { throw new AssertionError(rejected); }));
    }
}
