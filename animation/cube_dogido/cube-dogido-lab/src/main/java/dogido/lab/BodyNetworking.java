package dogido.lab;

import com.google.gson.Gson;
import com.google.gson.JsonParseException;
import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.server.network.ServerPlayerEntity;

/** 実送信者を確認してC2S要求をserver身体へ渡し、不変snapshotをS2Cで返す。JSON不正・版違いを境界で止め、ゲーム処理はserver threadへ配送する。 */
final class BodyNetworking {
    private static final Gson JSON = new Gson();
    private BodyNetworking() {}
    static void initialize() {
        PayloadTypeRegistry.playC2S().register(BodyPackets.Request.ID,BodyPackets.Request.CODEC);
        PayloadTypeRegistry.playS2C().register(BodyPackets.Response.ID,BodyPackets.Response.CODEC);
        ServerPlayNetworking.registerGlobalReceiver(BodyPackets.Request.ID,(payload,context) ->
            context.server().execute(() -> receive(context.player(),payload)));
    }
    private static void receive(ServerPlayerEntity player, BodyPackets.Request payload) {
        if (player.getEntityWorld().getServer().getPlayerManager().getPlayer(player.getUuid()) != player || player.isRemoved()) return;
        try {
            BodyPackets.Message message = JSON.fromJson(payload.json(),BodyPackets.Message.class);
            if (message == null || message.version() != dogido.lab.api.BodyProtocol.VERSION || message.kind() == null) return;
            DogidoEntity body = BodyManager.body(player);
            if (body != null && player.isAlive() && !player.isSpectator()) {
                switch (message.kind()) {
                    case "state" -> body.applyState(player,message.state());
                    case "action" -> body.requestAction(player,message.action());
                    case "owner_work" -> body.observeOwnerWork(player,message.ownerWork());
                    case "speech_gaze" -> body.observeSpeechGaze(player,message.speechGaze());
                    case "snapshot" -> { }
                    default -> { return; }
                }
            }
            if (ServerPlayNetworking.canSend(player,BodyPackets.Response.ID))
                ServerPlayNetworking.send(player,new BodyPackets.Response(BodySnapshotPayload.encode(BodyManager.snapshot(player))));
        } catch (JsonParseException | IllegalArgumentException ignored) {
            // A malformed client request cannot mutate another owner's body or stop the server tick.
        }
    }
}
