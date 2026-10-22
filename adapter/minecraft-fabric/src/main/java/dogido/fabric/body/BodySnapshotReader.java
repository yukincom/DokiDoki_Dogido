package dogido.fabric.body;

import dogido.lab.api.BodyBridge;
import dogido.lab.api.BodyProtocol;
import net.fabricmc.loader.api.FabricLoader;

/** API v10導入時だけロードする接続実体。snapshot・MOVE_TO・作業・発話注視を同じbridgeへ接続する。 */
final class BodySnapshotReader {
    private BodySnapshotReader() {}
    static BodyConnection.Source open() {
        var entries = FabricLoader.getInstance().getEntrypoints(BodyProtocol.ENTRYPOINT,BodyBridge.class);
        if (entries.size() != 1 || entries.getFirst().protocolVersion() != BodyProtocol.VERSION) return null;
        BodyBridge bridge = entries.getFirst();
        return new BodyConnection.Source() {
            public BodyConnection.Frame capture(java.util.UUID owner,String dimension) {
                if (!bridge.available()) return BodyConnection.Frame.unavailable("server_unavailable");
                var snapshot = bridge.snapshot();
                if (snapshot.isEmpty()) return BodyConnection.Frame.unavailable("stale_or_missing");
                var value = snapshot.get();
                var observation = BodyObservationJson.observed(value,bridge.snapshotAgeMillis(),owner,dimension);
                return new BodyConnection.Frame(observation,hearing(value),autonomy(value,observation));
            }
            public String lookAt(BodyConnection.LookRequest request) {
                var c = request.context(); var p = request.target();
                return bridge.requestAction(new BodyProtocol.ActionRequest(
                    new BodyProtocol.Context(c.ownerId(),c.bodyId(),c.epoch(),c.dimension()),
                    request.sequence(),request.actionId(),request.observedServerTick(),request.validForTicks(),
                    BodyProtocol.Action.LOOK_AT,new BodyProtocol.Position(p.x(),p.y(),p.z()),null))
                    .name().toLowerCase(java.util.Locale.ROOT);
            }
            public String moveTo(BodyConnection.MoveRequest request) {
                var context = request.context(); var point = request.destination();
                return bridge.requestAction(new BodyProtocol.ActionRequest(
                    new BodyProtocol.Context(context.ownerId(),context.bodyId(),context.epoch(),context.dimension()),
                    request.sequence(),request.actionId(),request.observedServerTick(),request.validForTicks(),
                    BodyProtocol.Action.MOVE_TO,point == null ? null : new BodyProtocol.Position(point.x(),point.y(),point.z()),null,
                    BodyProtocol.MovePurpose.valueOf(request.purpose().name())))
                    .name().toLowerCase(java.util.Locale.ROOT);
            }
            public void observeOwnerWork(boolean working) {
                BodySnapshotReader.observeOwnerWork(bridge,working);
            }
            public void observeSpeechGaze(BodyInspectProtocol.Context context,long sequence,long tick,net.minecraft.util.math.Vec3d point) {
                bridge.observeSpeechGaze(new BodyProtocol.SpeechGazeObservation(
                    new BodyProtocol.Context(context.ownerId(),context.bodyId(),context.epoch(),context.dimension()),sequence,tick,
                    point == null ? null : new BodyProtocol.Position(point.x,point.y,point.z)));
            }
        };
    }
    /** 現在の身体frameに束縛して送り、古いsnapshotのtickを現在時刻へ書き換えない。falseも送って作業終了を伝える。 */
    static void observeOwnerWork(BodyBridge bridge, boolean working) {
        if (!bridge.available()) return;
        var snapshot = bridge.snapshot().orElse(null);
        if (snapshot == null || snapshot.presence() != BodyProtocol.Presence.PRESENT
                || snapshot.context() == null || bridge.snapshotAgeMillis() > 750) return;
        bridge.observeOwnerWork(new BodyProtocol.OwnerWorkObservation(snapshot.context(),snapshot.serverTick(),working));
    }
    /** HTTP v3観測の検証を共有し、制御状態だけを独立のv2封筒へ投影する。 */
    static com.google.gson.JsonObject autonomy(BodyProtocol.Snapshot value,com.google.gson.JsonObject observation) {
        if (!"observed".equals(BodyInspectProtocol.string(observation,"status")))
            return BodyInspectProtocol.unavailable(BodyInspectProtocol.string(observation,"reason"));
        if (value.context().dimension().length() > 128) return BodyInspectProtocol.unavailable("invalid_context");
        var control = value.control();
        if (control == null || control.mode() == null || !Double.isFinite(control.followDistance())
                || !Double.isFinite(control.warpDistance()) || control.followDistance() <= 0 || control.followDistance() > 64 || control.warpDistance() < control.followDistance() || control.warpDistance() > 128)
            return BodyInspectProtocol.unavailable("invalid_control");
        var reassurance = control.reassurance();
        if (reassurance == null || !Double.isFinite(reassurance.value()) || reassurance.value() < 0 || reassurance.value() > 100
                || !Double.isFinite(reassurance.ownerDistance()) || reassurance.ownerDistance() < 0
                || reassurance.faceVisible() && !reassurance.ownerVisible())
            return BodyInspectProtocol.unavailable("invalid_reassurance");
        var row = new com.google.gson.JsonObject(); row.addProperty("version",2); row.addProperty("status","observed");
        row.add("context",observation.getAsJsonObject("sample").get("context").deepCopy());
        row.addProperty("server_tick",value.serverTick()); row.addProperty("snapshot_revision",value.snapshotRevision());
        row.add("age_ms",observation.get("age_ms").deepCopy()); row.addProperty("enabled",control.enabled());
        row.addProperty("mode",control.mode().name().toLowerCase(java.util.Locale.ROOT));
        row.addProperty("follow_distance",control.followDistance()); row.addProperty("warp_distance",control.warpDistance());
        row.addProperty("activity",value.activity().name().toLowerCase(java.util.Locale.ROOT)); row.addProperty("reason",wireReason(value.reason()));
        var feeling = new com.google.gson.JsonObject(); feeling.addProperty("value",reassurance.value());
        feeling.addProperty("owner_distance",reassurance.ownerDistance()); feeling.addProperty("owner_visible",reassurance.ownerVisible());
        feeling.addProperty("face_visible",reassurance.faceVisible()); feeling.addProperty("owner_working",reassurance.ownerWorking());
        row.add("reassurance",feeling);
        row.add("attention",new BodyAttention().observation(row));
        row.add("candidates",new com.google.gson.JsonArray());
        var result = value.actionResult();
        if (result != null && result.actionId() != null && result.outcome() != null && result.sequence() >= 0) {
            var action = new com.google.gson.JsonObject(); action.addProperty("action_id",result.actionId().toString());
            action.addProperty("sequence",result.sequence()); action.addProperty("outcome",result.outcome().name().toLowerCase(java.util.Locale.ROOT));
            action.addProperty("reason",wireReason(result.reason())); row.add("action_result",action);
        }
        return row;
    }

    private static String wireReason(String reason) {
        return reason != null && reason.matches("[a-z0-9_]{1,96}") ? reason : "unknown";
    }

    /** 発音tickとsequenceをそのまま運ぶ。繰り返しsnapshotに入っていても新しい音の時刻にしない。 */
    private static BodyConnection.Hearing hearing(BodyProtocol.Snapshot snapshot) {
        var hearing = snapshot.hearing();
        if (hearing == null || hearing.status() != BodyProtocol.ObservationStatus.OBSERVED
                || hearing.serverTick() != snapshot.serverTick() || hearing.sounds() == null || hearing.sounds().size() > 8)
            return BodyConnection.Hearing.unavailable();
        var sounds = new java.util.ArrayList<BodyConnection.HeardSound>();
        for (var sound : hearing.sounds()) {
            if (sound == null || sound.position() == null || sound.sequence() < 0 || sound.serverTick() < 0
                    || sound.serverTick() > snapshot.serverTick() || sound.soundId() == null || sound.soundId().length() > 256
                    || sound.category() == null || sound.category().length() > 32 || !Float.isFinite(sound.volume()) || sound.volume() < 0
                    || sound.pitch() != null && (!Float.isFinite(sound.pitch()) || sound.pitch() < 0)
                    || !Double.isFinite(sound.position().x()) || !Double.isFinite(sound.position().y()) || !Double.isFinite(sound.position().z()))
                return BodyConnection.Hearing.unavailable();
            sounds.add(new BodyConnection.HeardSound(sound.sequence(),sound.serverTick(),sound.position().x(),
                sound.position().y(),sound.position().z(),sound.soundId(),sound.category(),sound.volume(),sound.pitch(),sound.sourceEntityId()));
        }
        return new BodyConnection.Hearing(true,hearing.serverTick(),sounds,hearing.truncated());
    }
}
