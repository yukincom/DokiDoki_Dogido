package dogido.fabric.body;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dogido.fabric.observation.ObservationOrigin;
import dogido.fabric.observation.player.PlayerObserver;
import java.time.Instant;
import net.minecraft.client.MinecraftClient;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.player.PlayerEntity;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;

/** 位置付き実音が鳴った時点の根拠。遅配されたserver音へ現在の調査を付けない。 */
final class BodySoundContext {
    private static final int MAX_CANDIDATES = 32;
    private BodySoundContext() {}

    static JsonObject capture(MinecraftClient client, ObservationOrigin origin, Vec3d source,
            double range, long poseAgeMs, String soundId) {
        String id = soundId.replace("minecraft:", "");
        if (!id.startsWith("block.") || !(id.endsWith(".step") || id.endsWith(".break"))) return null;
        if (client.player == null || client.world == null) return null;
        String now = Instant.now().toString();
        JsonObject evidence = new JsonObject();
        evidence.addProperty("heard_at", now);
        evidence.add("source_position", point(source));
        JsonObject player = new JsonObject();
        player.addProperty("id", client.player.getUuidAsString());
        player.add("position", point(client.player.getEntityPos()));
        var velocity = client.player.getVelocity();
        String activity = PlayerObserver.blockBreakingActive(client) ? "breaking"
            : client.player.isOnGround() && !client.player.hasVehicle()
                && velocity.x*velocity.x+velocity.z*velocity.z > 0.000001 ? "walking" : "idle";
        player.addProperty("activity", activity);
        player.addProperty("observed_at", now);
        evidence.add("player", player);
        JsonObject survey = new JsonObject();
        survey.addProperty("scope", "hearing_range");
        survey.addProperty("observed_at", now);
        survey.add("receiver_position", point(origin.position()));
        survey.addProperty("radius_blocks", range);
        JsonArray others = new JsonArray();
        survey.add("other_emitters", others);
        String status = "unavailable";
        // 音の実到達半径を使い、古い身体姿勢・未ロード範囲を全域調査済みにしない。
        if (Double.isFinite(range) && range > 0 && range <= 64 && poseAgeMs >= 0 && poseAgeMs <= 100) {
            Vec3d p = origin.position();
            Box area = new Box(p.x-range,p.y-range,p.z-range,p.x+range,p.y+range,p.z+range);
            if (BodyOriginVision.loaded(client.world,area)) {
                status = "complete";
                for (var entity : client.world.getOtherEntities(null,area)) {
                    if (!(entity instanceof LivingEntity living) || !living.isAlive()
                            || entity.getUuid().equals(origin.threatTargetId())
                            || origin.distanceTo(entity) > range) continue;
                    // 現行の身体はnoClip移動で足音経路を通らず、block破壊操作も持たない。
                    // APIだけを参照するadapterなので実装の型名で限定する。他の身体実装は候補に残す。
                    if (origin.isSelf(entity) && entity.getClass().getName().equals("dogido.lab.DogidoEntity")
                            && (id.endsWith(".break") || entity.noClip)) continue;
                    // 一般モブは視認・移動速度で除外しない。
                    if (others.size() == MAX_CANDIDATES) { status = "truncated"; break; }
                    JsonObject candidate = new JsonObject();
                    candidate.addProperty("kind", entity instanceof PlayerEntity ? "player" : "mob");
                    candidate.addProperty("id", entity.getUuidAsString());
                    candidate.add("position", point(entity.getEntityPos()));
                    others.add(candidate);
                }
            }
        }
        survey.addProperty("status", status);
        evidence.add("survey", survey);
        return evidence;
    }
    private static JsonObject point(Vec3d value) {
        JsonObject result = new JsonObject();
        result.addProperty("x",value.x); result.addProperty("y",value.y); result.addProperty("z",value.z);
        return result;
    }
}
