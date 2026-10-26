package dogido.fabric.body;

import net.minecraft.client.MinecraftClient;
import static dogido.fabric.body.BodyInspectProtocol.*;

/**
 * client threadの実身体snapshotと本人の目の位置を既存向き変更器へ渡す。
 */
public final class BodyLookRuntime {
    private BodyLookRuntime() {}
    public static BodyLookController create(BodyConnection connection) { return create(connection,connection::lookAt); }
    public static BodyLookController create(BodyConnection connection,java.util.function.Function<BodyConnection.LookRequest,String> submitter) {
        return new BodyLookController(new BodyLookController.Access() {
            public BodyLookController.Live capture() {
                MinecraftClient client = MinecraftClient.getInstance();
                if (!client.isOnThread() || client.player == null || client.world == null || !client.player.isAlive()) return null;
                var frame = connection.captureFrame(client.player.getUuid(),client.world.getRegistryKey().getValue().toString());
                var observation = frame.observation();
                if (!"observed".equals(string(observation,"status"))) return null;
                var sample = observation.getAsJsonObject("sample"); var pose = sample.getAsJsonObject("self_pose");
                var result = frame.autonomy().getAsJsonObject("action_result");
                long prior=result==null?0:result.get("sequence").getAsLong();
                if(frame.lookResults()!=null)for(var row:frame.lookResults().entrySet())
                    prior=Math.max(prior,row.getValue().getAsJsonObject().get("sequence").getAsLong());
                return new BodyLookController.Live(context(sample.getAsJsonObject("context")),sample.get("server_tick").getAsLong(),
                    observation.get("age_ms").getAsLong(),prior,
                    point(pose.getAsJsonObject("eye_position")),pose.get("view_yaw").getAsDouble(),
                    new Point(client.player.getX(),client.player.getEyeY(),client.player.getZ()));
            }
            public long nextSequence(Context context,long prior,long tick,long last) { return connection.nextActionSequence(context,prior,tick); }
            public String submit(BodyConnection.LookRequest request) { return submitter.apply(request); }
        });
    }
}
