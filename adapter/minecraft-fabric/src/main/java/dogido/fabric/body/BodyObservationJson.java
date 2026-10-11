package dogido.fabric.body;

import com.google.gson.JsonObject;
import dogido.lab.api.BodyProtocol;
import dogido.lab.api.BodyProtocol.*;
import java.util.Locale;
import java.util.UUID;

/** 不変Body APIからHTTP v1契約へ投影する。座標はblock、yawは度、ageはms。主語と世界を照合してから値を送る。 */
public final class BodyObservationJson {
    private BodyObservationJson() {}

    public static JsonObject unavailable(String reason) {
        return BodyConnection.unavailable(reason);
    }

    /** 未取得値を原点に変換しない。server_poseはサーバー実測であり、見通しのある視認を意味しない。 */
    public static JsonObject observed(Snapshot s, long ageMs, UUID owner, String dimension) {
        // 版・存在・owner・世界を先に照合する。client側player座標で身体の欠損を補わない。
        if (s.version() != BodyProtocol.VERSION) return unavailable("version_mismatch");
        if (s.presence() == null) return unavailable("invalid_snapshot");
        if (s.presence() != Presence.PRESENT) {
            return unavailable(s.presence().name().toLowerCase(Locale.ROOT));
        }
        Context c = s.context();
        if (c == null || c.bodyId() == null || c.epoch() == null || !owner.equals(c.ownerId())) {
            return unavailable("owner_mismatch");
        }
        if (!dimension.equals(c.dimension()) || !dimension.equals(s.ownerDimension())) {
            return unavailable("dimension_mismatch");
        }
        // 3秒未満の同一snapshotだけを採用する。非有限値・欠損は座標なしで返す。
        if (ageMs < 0 || ageMs >= 3000) return unavailable("stale_or_missing");
        if (s.serverTick() < 0 || s.snapshotRevision() < 0 || !valid(s.position()) || !Float.isFinite(s.yaw())
                || s.pose() == null || !valid(s.pose().eyePosition()) || !valid(s.pose().ownerPosition())) {
            return unavailable("invalid_snapshot");
        }

        // 配送照合に必要な識別子と実測をsampleへ閉じる。Rust側で会話用材料へ投影する。
        JsonObject context = new JsonObject();
        context.addProperty("owner_id",c.ownerId().toString());
        context.addProperty("body_id",c.bodyId().toString());
        context.addProperty("epoch",c.epoch().toString());
        context.addProperty("dimension",c.dimension());
        JsonObject sample = new JsonObject();
        sample.add("context",context);
        sample.addProperty("server_tick",s.serverTick());
        sample.addProperty("snapshot_revision",s.snapshotRevision());

        JsonObject self = new JsonObject();
        self.add("position",position(s.position()));
        self.add("eye_position",position(s.pose().eyePosition()));
        // Minecraftの連続回転値を[-180,180)度に揃える。playerのyawはここへ入れない。
        double yaw = ((s.yaw() % 360.0) + 540.0) % 360.0 - 180.0;
        self.addProperty("yaw",yaw);
        sample.add("self_pose",self);
        JsonObject player = new JsonObject();
        player.add("position",position(s.pose().ownerPosition()));
        sample.add("player_pose",player);

        JsonObject json = new JsonObject();
        json.addProperty("version",1);
        json.addProperty("status","observed");
        json.addProperty("basis","server_pose");
        json.addProperty("age_ms",ageMs);
        json.add("sample",sample);
        return json;
    }

    private static boolean valid(Position p) {
        return p != null && Double.isFinite(p.x()) && Double.isFinite(p.y()) && Double.isFinite(p.z())
            && Math.abs(p.x()) <= 30_000_000 && Math.abs(p.y()) <= 30_000_000 && Math.abs(p.z()) <= 30_000_000;
    }

    private static JsonObject position(Position p) {
        JsonObject json = new JsonObject();
        json.addProperty("x",p.x());
        json.addProperty("y",p.y());
        json.addProperty("z",p.z());
        return json;
    }
}
