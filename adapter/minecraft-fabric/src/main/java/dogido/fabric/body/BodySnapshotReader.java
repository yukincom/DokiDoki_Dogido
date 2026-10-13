package dogido.fabric.body;

import dogido.lab.api.BodyBridge;
import dogido.lab.api.BodyProtocol;
import net.fabricmc.loader.api.FabricLoader;

/** API v5導入時だけロードする接続実体。client threadでsnapshotと受信経過msを続けて読む。操作APIは呼ばない。 */
final class BodySnapshotReader {
    private BodySnapshotReader() {}
    static BodyConnection.Source open() {
        var entries = FabricLoader.getInstance().getEntrypoints(BodyProtocol.ENTRYPOINT,BodyBridge.class);
        if (entries.size() != 1 || entries.getFirst().protocolVersion() != BodyProtocol.VERSION) return null;
        BodyBridge bridge = entries.getFirst();
        return (owner,dimension) -> {
            if (!bridge.available()) return BodyConnection.Frame.unavailable("server_unavailable");
            var snapshot = bridge.snapshot();
            if (snapshot.isEmpty()) return BodyConnection.Frame.unavailable("stale_or_missing");
            var value = snapshot.get();
            return new BodyConnection.Frame(BodyObservationJson.observed(value,bridge.snapshotAgeMillis(),owner,dimension),
                hearing(value));
        };
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
