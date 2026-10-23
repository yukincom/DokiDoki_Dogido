package dogido.lab;

import com.google.gson.Gson;
import dogido.lab.api.BodyProtocol.HearingObservation;
import dogido.lab.api.BodyProtocol.Snapshot;
import dogido.lab.api.BodyProtocol.RecallReceipt;
import java.nio.charset.StandardCharsets;

/**
 * snapshotを既存packetの上限内へ直列化する。追加聴覚だけを古い順に省き、pose・owner視認・食料や操作結果は変更しない。
 * 音を省いた場合はtruncatedを保持する。既存部分だけで上限を超える不正値は送信前に拒否する。
 */
final class BodySnapshotPayload {
    static final int MAX_BYTES = 8192;
    private static final Gson JSON = new Gson();
    private BodySnapshotPayload() {}

    static String encode(Snapshot snapshot) { return encode(snapshot,null); }
    /** 呼び寄せのreceiptも同じ送信上限へ含め、操作結果を省くことなく聴覚だけを調整する。 */
    static String encode(Snapshot snapshot, RecallReceipt recall) {
        Snapshot bounded = snapshot;
        while (true) {
            var envelope = JSON.toJsonTree(bounded).getAsJsonObject();
            if (recall != null) envelope.add("recall_result",JSON.toJsonTree(recall));
            String text = JSON.toJson(envelope);
            if (text.length() <= MAX_BYTES && text.getBytes(StandardCharsets.UTF_8).length <= MAX_BYTES) return text;
            HearingObservation hearing = bounded.hearing();
            if (hearing == null || hearing.sounds().isEmpty())
                throw new IllegalArgumentException("body_snapshot_too_large");
            bounded = withHearing(bounded,new HearingObservation(hearing.status(),hearing.serverTick(),
                hearing.sounds().subList(1,hearing.sounds().size()),true));
        }
    }

    /** 共通snapshotの実値は一切更新せず、送信枠に入る音のリストだけを狭める。 */
    private static Snapshot withHearing(Snapshot s, HearingObservation hearing) {
        return new Snapshot(s.version(),s.presence(),s.context(),s.ownerDimension(),s.serverTick(),s.snapshotRevision(),
            s.position(),s.yaw(),s.activity(),s.reason(),s.appliedStateRevision(),s.stateResult(),s.actionResult(),
            s.sensors(),s.vitals(),s.food(),s.pose(),s.vision(),hearing,s.control());
    }
}
