package dogido.fabric.body;

import java.util.LinkedHashMap;
import java.util.Objects;
import java.util.UUID;
import static dogido.fabric.body.BodyInspectProtocol.*;

/**
 * 現在の身体向きとプレイヤー座標から注視点を作り、LOOK_ATへ渡す。座標はblock、yawは度。
 * TODO(BERT): 分類完成後にdirection/playerへ接続する。自然文の解析・現行5分類の振り分けは追加しない。
 * 送信結果queuedは回転成功ではない。呼出側は同じcontext/action ID/sequenceの身体結果を待つ。
 */
public final class BodyLookController {
    /** ドギド自身の現在yawに対する8方向。Minecraftの正yawは本人の右回り。 */
    public enum Direction {
        FRONT(0), FRONT_RIGHT(45), RIGHT(90), BACK_RIGHT(135),
        BACK(180), BACK_LEFT(-135), LEFT(-90), FRONT_LEFT(-45);
        private final double degrees;
        Direction(double degrees) { this.degrees = degrees; }
    }
    public record Live(Context context,long tick,long ageMillis,long priorSequence,Point eye,double yaw,Point playerEye) {}
    public interface Access {
        Live capture();
        String submit(BodyConnection.LookRequest request);
    }
    public record Result(UUID actionId,long sequence,String status) {}
    private record Sent(Direction direction,BodyConnection.LookRequest request) {}
    private final Access access;
    private final LinkedHashMap<UUID,Sent> sent = new LinkedHashMap<>();
    private long lastSequence;
    public BodyLookController(Access access) { this.access = access; }
    /** 水平8block先の一点。pitchに左右されず、斜めも同じ長さにする。 */
    public static Point directionTarget(Point eye,double yaw,Direction direction) {
        if (eye == null || !Double.isFinite(yaw) || direction == null) throw new IllegalArgumentException("direction_pose");
        double angle = Math.toRadians(yaw+direction.degrees);
        return new Point(eye.x()-8*Math.sin(angle),eye.y(),eye.z()+8*Math.cos(angle));
    }
    /** TODO(BERT): 明示された8方向だけを渡す。基準は受付時の身体snapshotのview_yaw。 */
    public Result direction(Context expected,UUID actionId,Direction direction) {
        return direction == null ? new Result(actionId,-1,"invalid_direction") : send(expected,actionId,direction);
    }
    /** TODO(BERT): プレイヤー注視分類から接続する。現在の本人の目を送り、その座標へ一度向く。 */
    public Result player(Context expected,UUID actionId) { return send(expected,actionId,null); }
    private Result send(Context expected,UUID id,Direction direction) {
        if (expected == null || id == null) return new Result(id,-1,"invalid_request");
        Live live = access.capture();
        if (live == null || !Objects.equals(expected,live.context())) return new Result(id,-1,"stale_context");
        if (live.tick() < 0 || live.ageMillis() < 0 || live.ageMillis() > 750) return new Result(id,-1,"stale_snapshot");
        Sent previous = sent.get(id);
        if (previous != null) {
            if (previous.direction() != direction || !expected.equals(previous.request().context())) return new Result(id,-1,"action_id_conflict");
            // 再送は元の座標・観測tickを維持し、右へ向く命令を二度の相対回転にしない。
            var request = previous.request();
            return new Result(id,request.sequence(),access.submit(request));
        }
        Point target;
        try { target = direction == null ? live.playerEye() : directionTarget(live.eye(),live.yaw(),direction); }
        catch (IllegalArgumentException error) { return new Result(id,-1,"invalid_pose"); }
        if (target == null || live.eye() == null || live.eye().distance(target) > 64) return new Result(id,-1,"invalid_target");
        long sequence = Math.max(Math.max(lastSequence,live.priorSequence()),live.tick())+1;
        if (sequence <= 0) return new Result(id,-1,"invalid_sequence");
        var request = new BodyConnection.LookRequest(expected,sequence,id,live.tick(),20,target);
        lastSequence = sequence;
        sent.put(id,new Sent(direction,request));
        while (sent.size() > 32) sent.remove(sent.keySet().iterator().next());
        return new Result(id,sequence,access.submit(request));
    }
    /** 接続終了時に呼ぶ。古いcontextの要求を次の世界へ付け直さない。 */
    public void clear() { sent.clear(); lastSequence = 0; }
}
