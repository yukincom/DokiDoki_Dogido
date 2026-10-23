package dogido.fabric.body;

import com.google.gson.JsonObject;
import dogido.fabric.protocol.DogidoEventClient.SessionRef;
import java.time.Instant;
import java.time.Clock;
import java.util.LinkedHashSet;
import java.util.UUID;
import java.util.function.Consumer;
import java.util.function.Predicate;
import static dogido.fabric.body.BodyInspectProtocol.*;

/** 身体観察命令を一件ずつ検証・配送し、同じaction IDの終端結果だけをHTTP結果として確定する。 */
final class BodyInspectController {
    record Live(JsonObject autonomy,Point body,Point owner,double ownerEyeHeight) {}
    record ResolvedTarget(Point position,BodyApproachGeometry.Bounds targetBounds,
            BodyApproachGeometry.Bounds bodyShape,boolean ceiling) {}
    interface Access {
        Live capture();
        default long nextSequence(Context context,long prior,long tick,long last) { return Math.max(Math.max(last,prior),tick)+1; }
        /** 現在の生存・種類・非敵対・視認を再検査し、実座標を返す。未確認はnull。 */
        ResolvedTarget resolve(Target target,Live live);
        /** 同じsessionの撮影時対応表へ見た目の属性を問い合わせる。通常候補表とは独立に現物を確定する。 */
        default Target imageTarget(Command command,SessionRef session,Live live) { return null; }
        default ResolvedTarget resolveImage(Target target,Live live) { return resolve(target,live); }
        boolean collisionFree(BodyApproachGeometry.Bounds destination);
        String moveTo(SessionRef session,Command command,BodyConnection.MoveRequest request);
    }
    private record Pending(Command command,SessionRef session,long sequence,Instant deadline) {}
    private final Access access;
    private final Predicate<SessionRef> current;
    private final Consumer<JsonObject> resultSink;
    private final Clock clock;
    private final LinkedHashSet<UUID> seen = new LinkedHashSet<>();
    private Pending pending;
    private long lastSequence;
    BodyInspectController(Access access,Predicate<SessionRef> current,Consumer<JsonObject> resultSink) {
        this(access,current,resultSink,Clock.systemUTC());
    }
    BodyInspectController(Access access,Predicate<SessionRef> current,Consumer<JsonObject> resultSink,Clock clock) {
        this.access=access; this.current=current; this.resultSink=resultSink; this.clock=clock;
    }
    /** client threadから呼ぶ。応答待ち中の世界世代と、生成前候補の座標を再照合して一度だけ送る。 */
    void handle(Command command,SessionRef session,Instant now) {
        if (!current.test(session) || !seen.add(command.commandId())) return;
        while (seen.size() > 2048) seen.remove(seen.iterator().next());
        String timeError=command.timeError(now);
        if (timeError != null) { finish(command,"expired",timeError,now); return; }
        if (pending != null) { finish(command,"rejected","body_action_pending",now); return; }
        try {
            Live live=access.capture(); String invalid=invalid(live,command.context());
            if (invalid != null) { finish(command,"rejected",invalid,now); return; }
            boolean fromImage=command.imageTarget()!=null;
            if(fromImage) {
                Target grounded=access.imageTarget(command,session,live);
                if(grounded==null) {finish(command,"rejected","image_target_unresolved",now);return;}
                command=new Command(command.commandId(),command.issuedAt(),command.expiresAt(),command.context(),grounded,Purpose.INSPECT);
            }
            double followDistance=live.autonomy().get("follow_distance").getAsDouble();
            Point destination=null;
            if (command.purpose() == Purpose.INSPECT) {
                Target selected=fromImage?command.target():null;
                for (var row : live.autonomy().getAsJsonArray("candidates")) {
                    if(fromImage)break;
                    Target candidate=target(row.getAsJsonObject());
                    if (candidate.id().equals(command.target().id()) && candidate.kind().equals(command.target().kind())
                            && candidate.typeId().equals(command.target().typeId())) { selected=candidate; break; }
                }
                if (selected == null) { finish(command,"rejected","target_not_observed",now); return; }
                double tolerance="block".equals(selected.kind()) ? .01 : 1.5;
                if (selected.position().distance(command.target().position()) > tolerance) {
                    finish(command,"rejected","target_moved",now); return;
                }
                ResolvedTarget resolved=fromImage?access.resolveImage(selected,live):access.resolve(selected,live);
                if (resolved == null) { finish(command,"rejected","target_unavailable",now); return; }
                Point actual=resolved.position();
                if (actual.distance(command.target().position()) > tolerance) { finish(command,"rejected","target_moved",now); return; }
                if (!Double.isFinite(followDistance) || followDistance <= 0
                        || actual.distance(live.owner()) > followDistance || live.body().distance(live.owner()) > followDistance) {
                    finish(command,"rejected","outside_owner_range",now); return;
                }
                destination=BodyApproachGeometry.choose(live.body(),live.owner(),live.ownerEyeHeight(),followDistance,resolved.targetBounds(),resolved.bodyShape(),
                    resolved.ceiling(),access::collisionFree);
                if (destination == null) { finish(command,"rejected","no_safe_approach",now); return; }
            } else {
                // ownerのidentityは同じframeのcontextで確定済み。目的地・足場・作業中の正面は身体serverで再検査する。
                var reassurance=live.autonomy().getAsJsonObject("reassurance");
                if (reassurance == null) { finish(command,"rejected","invalid_reassurance",now); return; }
                if (command.purpose() == Purpose.OWNER_FACE && reassurance.get("owner_working").getAsBoolean()) {
                    finish(command,"rejected","owner_working",now); return;
                }
                double warpDistance=live.autonomy().get("warp_distance").getAsDouble();
                double emergency=Math.min(warpDistance,Math.max(followDistance*1.5,followDistance+4));
                if (!Double.isFinite(followDistance) || !Double.isFinite(warpDistance) || !Double.isFinite(emergency) || followDistance <= 0 || warpDistance < followDistance
                        || live.body().distance(live.owner()) > emergency) {
                    finish(command,"rejected","outside_owner_range",now); return;
                }
            }
            long tick=live.autonomy().get("server_tick").getAsLong();
            long prior=live.autonomy().has("action_result") ? live.autonomy().getAsJsonObject("action_result").get("sequence").getAsLong() : 0;
            long sequence=access.nextSequence(command.context(),prior,tick,lastSequence);
            if (sequence <= 0) { finish(command,"rejected","invalid_sequence",now); return; }
            var request=new BodyConnection.MoveRequest(command.context(),sequence,command.commandId(),tick,100,destination,command.purpose());
            // 視認と衝突検査の間にもHTTP callbackは世代を更新できる。送信直前に世代と実時計を再照合する。
            if (!current.test(session)) return;
            Instant submittedAt=clock.instant();
            String finalTimeError=command.timeError(submittedAt);
            if (finalTimeError != null) { finish(command,"expired",finalTimeError,submittedAt); return; }
            // 本番AccessはEventClientの同じsession monitor内でも再照合し、世代切替とqueueを線形化する。
            String submission=access.moveTo(session,command,request);
            if ("stale_session".equals(submission)) return;
            if (!"queued".equals(submission)) {
                String status=java.util.Set.of("expired","issued_in_future","invalid_lifetime").contains(submission) ? "expired" : "rejected";
                finish(command,status,submission,clock.instant()); return;
            }
            lastSequence=sequence;
            // QUEUEDは配送受付。身体側のSUCCEEDEDを受信するまで到着の結果を作らない。
            pending=new Pending(command,session,sequence,command.issuedAt().plusSeconds(35));
        } catch (RuntimeException | LinkageError error) { finish(command,"failed","bridge_error",now); }
    }
    void tick(Instant now) {
        Pending work=pending; if (work == null) return;
        if (!current.test(work.session())) { pending=null; return; }
        if (!now.isBefore(work.deadline())) { pending=null; finish(work.command(),"expired","result_timeout",now); return; }
        try {
            Live live=access.capture();
            String invalid=invalid(live,work.command().context());
            if (invalid != null) { pending=null; finish(work.command(),"rejected",invalid,now); return; }
            JsonObject result=live.autonomy().getAsJsonObject("action_result");
            if (result != null && work.command().commandId().toString().equals(string(result,"action_id"))
                    && result.get("sequence").getAsLong() == work.sequence()) {
                String outcome=string(result,"outcome");
                String status=switch (outcome == null ? "" : outcome) {
                    case "succeeded" -> "succeeded";
                    case "rejected" -> "rejected";
                    case "interrupted" -> "failed";
                    case "expired" -> "expired";
                    default -> null;
                };
                if (status != null) { pending=null; finish(work.command(),status,string(result,"reason"),now); return; }
            }
        } catch (RuntimeException | LinkageError error) { pending=null; finish(work.command(),"failed","bridge_error",now); }
    }
    void clear() { pending=null; }
    boolean pending() { return pending != null; }
    private static String invalid(Live live,Context expected) {
        if (live == null || live.autonomy() == null || !"observed".equals(string(live.autonomy(),"status"))) return "body_unavailable";
        JsonObject row=live.autonomy();
        if (row.get("version").getAsInt() != 2) return "version_mismatch";
        if (!expected.equals(context(row.getAsJsonObject("context")))) return "stale_context";
        long age=row.get("age_ms").getAsLong();
        if (age < 0 || age >= 3_000) return "stale_snapshot";
        if (!row.get("enabled").getAsBoolean() || !"follow".equals(string(row,"mode"))) return "control_not_follow";
        if (live.body() == null || live.owner() == null) return "invalid_pose";
        return null;
    }
    private void finish(Command command,String status,String detail,Instant now) {
        resultSink.accept(result(command,status,detail == null ? "unknown" : detail,now));
    }
}
