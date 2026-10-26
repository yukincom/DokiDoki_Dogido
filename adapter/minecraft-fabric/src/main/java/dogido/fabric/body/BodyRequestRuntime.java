package dogido.fabric.body;

import com.google.gson.JsonObject;
import dogido.fabric.protocol.DogidoEventClient.SessionRef;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.LinkedHashSet;
import java.util.UUID;
import java.util.function.BiConsumer;
import java.util.function.BiPredicate;
import java.util.function.Supplier;
import net.minecraft.client.MinecraftClient;
import net.minecraft.util.hit.HitResult;
import static dogido.fabric.body.BodyInspectProtocol.*;
import dogido.fabric.body.BodyRequestProtocol.Command;
import dogido.fabric.body.BodyRequestProtocol.Mode;

/** BERTへ変更済み。旧判定へ戻さない。分類済み操作を実姿勢と同一sessionに束縛して一度だけ配送する。 */
public final class BodyRequestRuntime {
    @FunctionalInterface public interface Sender {
        String submit(SessionRef session,Command command,Supplier<String> action);
    }
    private record Pending(Command command,SessionRef session,UUID connectionId,long sequence,Instant deadline,
                           BodyConnection.LookRequest look) {}
    private final BodyConnection connection;
    private final BiPredicate<SessionRef,Long> current;
    private final Sender sender;
    private final BiConsumer<SessionRef,JsonObject> resultSink;
    private final BodyLookController look;
    private final LinkedHashSet<UUID> seen=new LinkedHashSet<>();
    private final dogido.fabric.screenshot.BodyViewAfterLook lookCaptures=new dogido.fabric.screenshot.BodyViewAfterLook();
    private Pending pending;
    private BodyConnection.LookRequest dispatchLook;
    private Command dispatchCommand;
    private SessionRef dispatchSession;
    public BodyRequestRuntime(BodyConnection connection,BiPredicate<SessionRef,Long> current,Sender sender,
            BiConsumer<SessionRef,JsonObject> resultSink) {
        this.connection=connection; this.current=current; this.sender=sender; this.resultSink=resultSink;
        this.look=BodyLookRuntime.create(connection,request -> {
            String status=sender.submit(dispatchSession,dispatchCommand,() -> connection.lookAt(request));
            if("queued".equals(status))dispatchLook=request;
            return status;
        });
    }
    public void handle(Command command,SessionRef session) {
        MinecraftClient client=MinecraftClient.getInstance();
        client.execute(() -> handleOnClient(client,command,session));
    }
    private void handleOnClient(MinecraftClient client,Command command,SessionRef session) {
        if (!current.test(session,command.inputGeneration()) || !seen.add(command.commandId())) return;
        lookCaptures.clear(); // 新しい操作が失敗しても、その前に向いた画像を新操作の結果にしない。
        while (seen.size()>2048) seen.remove(seen.iterator().next());
        Instant now=Instant.now(); String problem=command.timeError(now);
        if (problem != null) { finish(command,session,"expired",problem,command.context()); return; }
        if (!ownerMatches(client,command)) { finish(command,session,"rejected","stale_owner_or_dimension",command.context()); return; }
        if (pending != null && !current.test(pending.session(),pending.command().inputGeneration())) discardPending();
        if (pending != null) { finish(command,session,"rejected","body_action_pending",command.context()); return; }
        try {
            UUID connectionId=UUID.nameUUIDFromBytes((session.sessionId()+":"+session.worldEpoch()).getBytes(StandardCharsets.UTF_8));
            long sequence=-1; String submission;
            if (command.mode()==Mode.RECALL) {
                var request=new BodyConnection.RecallRequest(command.ownerId(),connectionId,command.commandId(),command.dimension());
                submission=sender.submit(session,command,() -> connection.recall(request));
            } else {
                dispatchCommand=command; dispatchSession=session;
                BodyLookController.Result result;
                if (command.mode()==Mode.LOOK_PLAYER) result=look.player(command.context(),command.commandId());
                else if (command.mode()==Mode.LOOK_TARGET || command.mode()==Mode.LOOK_DIRECTION) {
                    Point target=target(client,command);
                    if (target==null) { finish(command,session,"rejected","target_unavailable",command.context()); return; }
                    result=look.point(command.context(),command.commandId(),target);
                } else result=look.direction(command.context(),command.commandId(),
                    BodyLookController.Direction.valueOf(command.mode().name().substring("LOOK_RELATIVE_".length())));
                sequence=result.sequence(); submission=result.status();
            }
            if ("stale_session".equals(submission) || "stale_input_generation".equals(submission)) return;
            if (!"queued".equals(submission)) {
                finish(command,session,java.util.Set.of("expired","issued_in_future","invalid_lifetime").contains(submission)?"expired":"rejected",submission,command.context());
                return;
            }
            // queuedは成功ではない。同じ実行IDの終端結果を待つ。
            pending=new Pending(command,session,connectionId,sequence,command.issuedAt().plusSeconds(35),dispatchLook);
        } catch (RuntimeException | LinkageError error) {
            connection.release(command.commandId());
            finish(command,session,"failed","bridge_error",command.context());
        } finally { dispatchCommand=null; dispatchSession=null; dispatchLook=null; }
    }
    private Point target(MinecraftClient client,Command command) {
        Point hitPoint=null;
        if (command.mode()==Mode.LOOK_TARGET) {
            HitResult hit=client.crosshairTarget;
            if(hit!=null && hit.getType()!=HitResult.Type.MISS) {
                var p=hit.getPos();hitPoint=new Point(p.x,p.y,p.z);
            }
            // 景色・空・遠景は物体への命中を必要としない。本人が向いている方向を使う。
        }
        var frame=connection.captureFrame(command.ownerId(),command.dimension());
        var observation=frame.observation();
        if (!"observed".equals(string(observation,"status"))) return null;
        var sample=observation.getAsJsonObject("sample");
        if (!command.context().equals(context(sample.getAsJsonObject("context")))) return null;
        Point eye=point(sample.getAsJsonObject("self_pose").getAsJsonObject("eye_position"));
        var direction=client.player.getRotationVec(1.0f);
        return BodyLookController.sceneTarget(eye,new Point(direction.x,direction.y,direction.z),hitPoint);
    }
    /** 完了確認はrootが取得した現在frameを使う。RECALLの専用結果照会は従来の台帳へ行う。 */
    public void tick(BodyConnection.Frame frame) {
        Pending work=pending; if (work==null) return;
        if (!current.test(work.session(),work.command().inputGeneration())) { discardPending(); return; }
        if (!ownerMatches(MinecraftClient.getInstance(),work.command())) { discardPending(); return; }
        if (!Instant.now().isBefore(work.deadline())) { discardPending(); finish(work.command(),work.session(),"expired","result_timeout",work.command().context()); return; }
        try {
            String outcome=null, reason=null; Context resolved=work.command().context();
            BodyConnection.Frame completedFrame=null;
            if (work.command().mode()==Mode.RECALL) {
                var result=connection.recallResult(work.command().commandId());
                if (result==null || !work.command().commandId().equals(result.actionId()) || !work.connectionId().equals(result.connectionId())
                        || !work.command().ownerId().equals(result.ownerId()) || !work.command().dimension().equals(result.dimension())) return;
                resolved=result.context(); outcome=result.outcome(); reason=result.reason();
                if ("succeeded".equals(outcome) && (resolved==null || !work.command().ownerId().equals(resolved.ownerId())
                        || !work.command().dimension().equals(resolved.dimension()) || !"arrived".equals(reason))) {
                    outcome="failed"; reason="invalid_recall_result";
                }
            } else {
                var observation=frame.observation();
                if (!"observed".equals(string(observation,"status"))) { discardPending(); finish(work.command(),work.session(),"rejected","body_unavailable",resolved); return; }
                var actual=context(observation.getAsJsonObject("sample").getAsJsonObject("context"));
                if (!resolved.equals(actual)) { discardPending(); finish(work.command(),work.session(),"rejected","stale_context",resolved); return; }
                var result=frame.lookResult(work.command().commandId());
                if (result==null || !work.command().commandId().toString().equals(string(result,"action_id"))
                        || work.sequence()!=result.get("sequence").getAsLong()) return;
                outcome=string(result,"outcome"); reason=string(result,"reason");completedFrame=frame;
                if ("succeeded".equals(outcome) && !"facing_target".equals(reason)) { outcome="failed"; reason="invalid_look_result"; }
            }
            String status=switch (outcome==null?"":outcome) {
                case "succeeded" -> "succeeded"; case "rejected" -> "rejected";
                case "interrupted" -> "failed"; case "expired" -> "expired"; default -> null;
            };
            if (status!=null) {
                Instant executedAt=Instant.now();
                if("succeeded".equals(status) && "facing_target".equals(reason) && completedFrame!=null)
                    lookCaptures.completed(work.session(),work.command(),work.look(),executedAt);
                discardPending();
                finish(work.command(),work.session(),status,reason,resolved,executedAt);
            }
        } catch (RuntimeException | LinkageError error) { discardPending(); finish(work.command(),work.session(),"failed","bridge_error",work.command().context()); }
    }
    private static boolean ownerMatches(MinecraftClient client,Command command) {
        return client.isOnThread() && client.player!=null && client.world!=null && client.player.isAlive()
            && command.ownerId().equals(client.player.getUuid()) && command.dimension().equals(client.world.getRegistryKey().getValue().toString());
    }
    /** 撮影受付時に同じ入力世代と成功IDへ束縛する。撮影後の位置・角度は再検査しない。 */
    public dogido.fabric.screenshot.BodyViewAfterLook.Proof lookCapture(
            dogido.fabric.screenshot.BodyViewProtocol.Command command,SessionRef session) {
        if(command.afterLook()==null || !current.test(session,command.afterLook().inputGeneration()))return null;
        return lookCaptures.capture(command,session,Instant.now());
    }
    private void finish(Command command,SessionRef session,String status,String reason,Context context) {
        finish(command,session,status,reason,context,Instant.now());
    }
    private void finish(Command command,SessionRef session,String status,String reason,Context context,Instant executedAt) {
        if (current.test(session,command.inputGeneration()))
            resultSink.accept(session,BodyRequestProtocol.result(command,status,reason==null?"unknown":reason,executedAt,context));
    }
    private void discardPending() {
        Pending previous=pending; pending=null;
        if (previous != null) connection.release(previous.command().commandId());
    }
    public void clear() { discardPending(); dispatchCommand=null; dispatchSession=null; dispatchLook=null;
        lookCaptures.clear();look.clear(); seen.clear(); }
}
