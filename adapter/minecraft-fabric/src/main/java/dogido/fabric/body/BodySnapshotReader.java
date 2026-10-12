package dogido.fabric.body;

import dogido.lab.api.BodyBridge;
import dogido.lab.api.BodyProtocol;
import net.fabricmc.loader.api.FabricLoader;

/** API v4導入時だけロードする接続実体。client threadでsnapshotと受信経過msを続けて読む。操作APIは呼ばない。 */
final class BodySnapshotReader {
    private BodySnapshotReader() {}
    static BodyConnection.Source open() {
        var entries = FabricLoader.getInstance().getEntrypoints(BodyProtocol.ENTRYPOINT,BodyBridge.class);
        if (entries.size() != 1 || entries.getFirst().protocolVersion() != BodyProtocol.VERSION) return null;
        BodyBridge bridge = entries.getFirst();
        return (owner,dimension) -> {
            if (!bridge.available()) return BodyObservationJson.unavailable("server_unavailable");
            var snapshot = bridge.snapshot();
            return snapshot.isEmpty() ? BodyObservationJson.unavailable("stale_or_missing")
                : BodyObservationJson.observed(snapshot.get(),bridge.snapshotAgeMillis(),owner,dimension);
        };
    }
}
