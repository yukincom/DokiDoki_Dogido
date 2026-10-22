package dogido.fabric.body;

import com.google.gson.JsonObject;
import dogido.fabric.protocol.DialoguePayload;
import java.util.ArrayList;
import java.util.List;
import java.util.function.BooleanSupplier;
import java.util.function.LongSupplier;
import net.minecraft.util.math.Vec3d;

/**
 * 既存会話pollの実再生中対象を、同世代の現在視認点・発音点へ照合して身体bridgeへ渡す。
 * HTTP・world走査・移動を増やさない。音源は観測時の位置であり、隠れた個体の追跡には使わない。
 */
public final class BodySpeechGaze {
    public record Target(String observer, String kind, String id, Vec3d position) {}
    private record Reference(String observer, String kind, String id) {}
    private record Request(String turn, BodyInspectProtocol.Context context, List<Reference> targets) {}
    private final BodyConnection connection;
    private final LongSupplier clock;
    private Request request;
    private Reference selected;
    private boolean selectedOnce;
    private BooleanSupplier current;
    private long received, sequence, nextSend;
    private boolean sent;

    public BodySpeechGaze(BodyConnection connection) { this(connection,System::nanoTime); }
    BodySpeechGaze(BodyConnection connection, LongSupplier clock) { this.connection=connection; this.clock=clock; }

    /** snapshotのsession検証後に呼ぶ。空値は音声終了として即解除し、失敗したpollは時計を更新しない。 */
    public void receive(DialoguePayload.Snapshot snapshot, BooleanSupplier current) {
        Request next = parse(snapshot.voiceEnabled() ? snapshot.bodyGaze() : null);
        if (request == null || next == null || !request.turn().equals(next.turn()) || !request.context().equals(next.context())) {
            selected=null; selectedOnce=false; nextSend=0;
        }
        request=next; this.current=current; received=clock.getAsLong();
    }
    public void clear() { request=null; selected=null; selectedOnce=false; current=null; nextSend=0; }

    /** client tickで一度呼ぶ。fresh frameのtickをそのまま送り、同一音や古いHTTP応答の鮮度を作り直さない。 */
    public void tick(BodyConnection.Frame frame, List<Target> targets) {
        JsonObject row=frame.observation();
        if (!"observed".equals(BodyInspectProtocol.string(row,"status")) || row.get("age_ms").getAsLong() > 750) {
            clear(); return;
        }
        JsonObject sample=row.getAsJsonObject("sample");
        var context=BodyInspectProtocol.context(sample.getAsJsonObject("context"));
        long tick=sample.get("server_tick").getAsLong(), now=clock.getAsLong();
        Vec3d target=resolve(context,targets,now);
        if (target == null && !sent || target != null && now < nextSend) return;
        connection.observeSpeechGaze(context,++sequence,tick,target);
        sent=target != null; nextSend=now+250_000_000L;
    }
    Vec3d resolve(BodyInspectProtocol.Context context, List<Target> targets, long now) {
        if (request == null || current == null || !current.getAsBoolean() || now-received >= 1_200_000_000L
                || !request.context().equals(context)) { clear(); return null; }
        if (!selectedOnce) {
            for (Reference reference : request.targets()) {
                if (targets.stream().anyMatch(t -> matches(reference,t))) { selected=reference; break; }
            }
            selectedOnce=selected != null;
        }
        return targets.stream().filter(t -> matches(selected,t)).map(Target::position).findFirst().orElse(null);
    }
    private static boolean matches(Reference reference, Target target) {
        return reference != null && reference.observer().equals(target.observer()) && reference.kind().equals(target.kind()) && reference.id().equals(target.id());
    }
    private static Request parse(JsonObject row) {
        try {
            if (row == null) return null;
            String turn=BodyInspectProtocol.string(row,"turn_id");
            if (turn == null || turn.isBlank() || turn.length() > 256) return null;
            var context=BodyInspectProtocol.context(row.getAsJsonObject("context"));
            if (context == null) return null;
            var array=row.getAsJsonArray("targets");
            if (array.isEmpty() || array.size() > 16) return null;
            var targets=new ArrayList<Reference>();
            for (var value : array) {
                var item=value.getAsJsonObject();
                String observer=BodyInspectProtocol.string(item,"observer"), kind=BodyInspectProtocol.string(item,"kind"), id=BodyInspectProtocol.string(item,"id");
                if (!("body".equals(observer) || "player".equals(observer)) || !("entity".equals(kind) || "sound".equals(kind)) || id == null || id.isBlank() || id.length() > 256) return null;
                targets.add(new Reference(observer,kind,id));
            }
            return new Request(turn,context,List.copyOf(targets));
        } catch (RuntimeException invalid) { return null; }
    }
}
