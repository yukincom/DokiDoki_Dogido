package dogido.fabric.body;

import net.minecraft.client.MinecraftClient;
import static dogido.fabric.body.BodyInspectProtocol.*;

/**
 * client threadの実身体snapshotと本人の目の位置を向き変更器へ渡す準備済み境界。
 * TODO(BERT): 完成後に生成・呼出・切断時clearを既存session検証下へ接続する。今は登録も送信も行わない。
 */
public final class BodyLookRuntime {
    private BodyLookRuntime() {}
    public static BodyLookController create(BodyConnection connection) {
        return new BodyLookController(new BodyLookController.Access() {
            public BodyLookController.Live capture() {
                MinecraftClient client = MinecraftClient.getInstance();
                if (!client.isOnThread() || client.player == null || client.world == null || !client.player.isAlive()) return null;
                var frame = connection.captureFrame(client.player.getUuid(),client.world.getRegistryKey().getValue().toString());
                var observation = frame.observation();
                if (!"observed".equals(string(observation,"status"))) return null;
                var sample = observation.getAsJsonObject("sample"); var pose = sample.getAsJsonObject("self_pose");
                var result = frame.autonomy().getAsJsonObject("action_result");
                return new BodyLookController.Live(context(sample.getAsJsonObject("context")),sample.get("server_tick").getAsLong(),
                    observation.get("age_ms").getAsLong(),result == null ? 0 : result.get("sequence").getAsLong(),
                    point(pose.getAsJsonObject("eye_position")),pose.get("view_yaw").getAsDouble(),
                    new Point(client.player.getX(),client.player.getEyeY(),client.player.getZ()));
            }
            public String submit(BodyConnection.LookRequest request) { return connection.lookAt(request); }
        });
    }
}
