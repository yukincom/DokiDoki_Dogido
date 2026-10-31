package dogido.fabric.screenshot;

import com.google.gson.*;
import java.awt.image.BufferedImage;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.time.Instant;
import java.util.Objects;
import java.util.Set;
import javax.imageio.ImageIO;

/** 見込み: 本体比較の三状態と参照をJSONへ移せばRustで同じ根拠を使える。
 * 合格: 指定状態・4枚前参照・セルを検査してevent化。範囲: 合成画素または指定保存画像、本体符号化・通信契約。GPU・HTTP・モデルは未確認。
 */
public final class BodyViewWireFixture {
    private static BufferedImage image(int color) {
        var image = new BufferedImage(64, 48, BufferedImage.TYPE_INT_RGB);
        for (int y = 0; y < 48; y++) for (int x = 0; x < 64; x++) image.setRGB(x, y, color);
        return image;
    }

    private static BufferedImage read(String path) throws Exception {
        var image = ImageIO.read(Path.of(path).toFile());
        if (image == null) throw new AssertionError("unreadable image: " + path);
        return image;
    }

    public static void main(String[] args) throws Exception {
        var input = JsonParser.parseReader(new InputStreamReader(System.in, StandardCharsets.UTF_8)).getAsJsonObject();
        var commands = BodyViewProtocol.parseCommands(input);
        if (commands.size() != 1) throw new AssertionError("exactly one real capture command required");
        var command = commands.get(0);
        var changes = new BodyViewChange();
        var scenario = input.get("scenario").getAsString();
        if (!Set.of("unknown", "unchanged", "changed").contains(scenario)) throw new AssertionError("unknown scenario: " + scenario);
        boolean realImages = input.has("before_path");
        if (realImages != input.has("after_path")) throw new AssertionError("before_path and after_path must be supplied together");
        var now = Instant.parse(input.get("captured_at").getAsString());
        var metadata = new JsonObject();
        metadata.addProperty("annotation_status", "owner_not_in_render_list");
        var previousPixels = realImages ? read(input.get("before_path").getAsString()) : image(0x808080);
        var previousImage = BodyViewImage.encode(previousPixels, metadata);
        BodyViewChange.Reference firstReference = null;
        if (!scenario.equals("unknown")) for (int i = 0; i < 4; i++) {
            var ref = new BodyViewChange.Reference("view_00000000-0000-0000-0000-%012d".formatted(100 + i),
                now.minusSeconds(4 - i), previousImage.sha256());
            if (i == 0) firstReference = ref;
            changes.observe("body", 1, previousPixels, ref);
        }
        var pixels = realImages ? read(input.get("after_path").getAsString()) : image(scenario.equals("changed") ? 0xffffff : 0x808080);
        var image = BodyViewImage.encode(pixels, metadata);
        var change = changes.observe("body", 1, pixels, new BodyViewChange.Reference(command.id(), now, image.sha256()));
        // 見込み: 未比較は変化ではなく、比較済みの2状態には4枚前が根拠として残る。
        // 合格: unknownはnull/空、unchangedは旧参照/空、changedは旧参照/非空。合成changedは全48マスlighter。範囲: 本体JavaからJSON。実画像も過去4回の受付は模擬。
        var expected = switch (scenario) {
            case "unknown" -> BodyViewChange.Status.NOT_COMPARED;
            case "unchanged" -> BodyViewChange.Status.UNCHANGED;
            default -> BodyViewChange.Status.CHANGED;
        };
        if (change.status() != expected || !Objects.equals(change.reference(), firstReference))
            throw new AssertionError("comparison status/reference: " + scenario);
        var comparison = change.json();
        if (!comparison.keySet().equals(Set.of("status", "reference", "cells"))) throw new AssertionError("comparison keys");
        if (firstReference == null ? !comparison.get("reference").isJsonNull() : !firstReference.json().equals(comparison.get("reference")))
            throw new AssertionError("wire reference");
        int cells = comparison.getAsJsonArray("cells").size();
        if (scenario.equals("changed") ? cells == 0 : cells != 0) throw new AssertionError("wire cell count");
        if (!realImages && scenario.equals("changed") && cells != 48) throw new AssertionError("synthetic changed cell count");
        for (var cell : comparison.getAsJsonArray("cells")) {
            var object = cell.getAsJsonObject();
            if (!object.keySet().equals(Set.of("row", "column", "local_change", "brightness"))) throw new AssertionError("wire cell keys");
            if (!realImages && (object.get("local_change").getAsBoolean() || !object.get("brightness").getAsString().equals("lighter")))
                throw new AssertionError("synthetic changed cell");
        }
        var view = new JsonObject();
        view.addProperty("png_base64", image.base64()); view.addProperty("sha256", image.sha256());
        view.addProperty("width", image.width()); view.addProperty("height", image.height());
        view.addProperty("captured_at", now.toString()); view.addProperty("capture_id", command.id());
        view.addProperty("body_entity_uuid", command.bodyId().toString()); view.addProperty("body_epoch", command.epoch().toString());
        view.addProperty("owner_uuid", command.ownerId().toString()); view.addProperty("dimension", command.dimension());
        view.addProperty("camera_restored", true); view.add("annotation_status", metadata.get("annotation_status"));
        view.addProperty("motion_sequence", command.afterMotionSequence() + 1);
        view.add("scene_comparison", comparison);
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
