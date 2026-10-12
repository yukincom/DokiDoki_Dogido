package dogido.fabric.body;

import com.google.gson.JsonObject;
import dogido.lab.api.BodyProtocol;
import dogido.lab.api.BodyProtocol.*;
import java.util.Locale;
import java.util.UUID;

/** 不変Body APIからHTTP v2契約へ投影する。座標はblock、角度は度、ageはms。姿勢と視認の根拠を分ける。 */
public final class BodyObservationJson {
    private BodyObservationJson() {}

    public static JsonObject unavailable(String reason) {
        return BodyConnection.unavailable(reason);
    }

    /** 未取得値を原点に変換しない。server_poseはサーバー実測であり、見通しのある視認を意味しない。 */
    public static JsonObject observed(Snapshot s, long ageMs, UUID owner, String dimension) {
        // 版・存在・owner・世界を先に照合する。client側player座標で身体の欠損を補わない。
        if (s == null) return unavailable("invalid_snapshot");
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
                || s.pose() == null || !valid(s.pose().eyePosition()) || !valid(s.pose().ownerPosition())
                || !Float.isFinite(s.pose().viewYaw()) || !Float.isFinite(s.pose().ownerYaw())
                || !validPitch(s.pose().viewPitch()) || !validPitch(s.pose().ownerPitch())
                || !validVision(s.vision(),s.serverTick())) {
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
        self.addProperty("yaw",yaw(s.yaw()));
        self.addProperty("view_yaw",yaw(s.pose().viewYaw()));
        self.addProperty("view_pitch",s.pose().viewPitch());
        sample.add("self_pose",self);
        JsonObject player = new JsonObject();
        player.add("position",position(s.pose().ownerPosition()));
        player.addProperty("yaw",yaw(s.pose().ownerYaw()));
        player.addProperty("pitch",s.pose().ownerPitch());
        sample.add("player_pose",player);
        // 同一tickの眼・視線と結び付ける。視覚だけUNAVAILABLEでも姿勢の実測は失わない。
        sample.add("vision",vision(s.vision()));

        JsonObject json = new JsonObject();
        json.addProperty("version",2);
        json.addProperty("status","observed");
        json.addProperty("pose_basis","server_pose");
        json.addProperty("age_ms",ageMs);
        json.add("sample",sample);
        return json;
    }

    /** APIの生yawは連続回転し得るため、二者のyawと視線を同じ範囲へ正規化する。 */
    private static double yaw(float value) { return ((value % 360.0)+540.0)%360.0-180.0; }
    private static boolean validPitch(float value) { return Float.isFinite(value) && value >= -90 && value <= 90; }

    /** 視認の確定/未取得の組合せと採取tickを照合し、古い視界や不正な理由を正常観測へ混ぜない。 */
    private static boolean validVision(VisionObservation v, long tick) {
        if (v == null || v.serverTick() != tick || v.playerVisibility() == null || v.reason() == null
                || !Double.isFinite(v.rangeBlocks()) || v.rangeBlocks() <= 0 || v.rangeBlocks() > 64
                || !Double.isFinite(v.horizontalFovDegrees()) || v.horizontalFovDegrees() <= 0 || v.horizontalFovDegrees() > 360
                || !Double.isFinite(v.verticalFovDegrees()) || v.verticalFovDegrees() <= 0 || v.verticalFovDegrees() > 180)
            return false;
        return switch (v.playerVisibility()) {
            case VISIBLE -> v.reason().equals("in_view");
            case NOT_VISIBLE -> java.util.Set.of("out_of_range","outside_fov","occluded").contains(v.reason());
            case UNAVAILABLE -> java.util.Set.of("area_unloaded","body_downed","owner_unavailable","invalid_pose").contains(v.reason());
        };
    }

    private static JsonObject vision(VisionObservation v) {
        JsonObject json = new JsonObject();
        json.addProperty("basis","body_eye_raycast_fov");
        json.addProperty("range_blocks",v.rangeBlocks());
        json.addProperty("horizontal_fov_degrees",v.horizontalFovDegrees());
        json.addProperty("vertical_fov_degrees",v.verticalFovDegrees());
        json.addProperty("player_visibility",v.playerVisibility().name().toLowerCase(Locale.ROOT));
        json.addProperty("reason",v.reason());
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
