package dogido.lab.control;

import dogido.lab.api.BodyProtocol.*;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;

/** 論理サーバー上でContext・順序・観測鮮度・期限を検査し、操作の受付結果と一度だけの実行許可を返す。状態と直近32要求の結果を所有し、実際の移動や食事は身体側が行う。 */
public final class ControlLease {
    public static final int MAX_LEASE_TICKS = 200;
    public static final int MAX_TRANSPORT_TICKS = 100;
    private Context context;
    private long revision = -1, lastActionSequence = -1, expiresAt;
    private boolean enabled, retreat, expired;
    private Mode mode = Mode.DIRECT;
    private StateResult stateResult;
    private ActionResult actionResult, activeAction, lookResult, activeLook;
    private ActionRequest activeRequest, activeLookRequest;
    private record Remembered(ActionRequest request, ActionResult result) {}
    public record ActionAdmission(ActionResult result, boolean execute) {}
    private final Map<java.util.UUID, Remembered> actions = new LinkedHashMap<>();

    public void reset(Context value) {
        context = value;
        revision = lastActionSequence = -1;
        expiresAt = 0;
        enabled = retreat = expired = false;
        mode = Mode.DIRECT;
        stateResult = null;
        actionResult = activeAction = lookResult = activeLook = null;
        activeRequest = activeLookRequest = null;
        actions.clear();
    }

    public StateResult apply(StateRequest request, long tick) {
        return apply(request,tick,null);
    }
    public StateResult apply(StateRequest request, long tick, String admissionError) {
        String error = checkContext(request == null ? null : request.context());
        if (error == null && (request.mode() == null || request.revision() < 0 || request.basedOnSnapshot() < 0)) error = "invalid_state";
        if (error == null && request.revision() <= revision) error = "old_revision";
        if (error == null && !fresh(request.observedServerTick(), tick, MAX_TRANSPORT_TICKS)) error = "stale_request";
        if (error == null && (request.leaseTicks() < 1 || request.leaseTicks() > MAX_LEASE_TICKS)) error = "invalid_lease";
        if (error == null && admissionError != null) error = admissionError;
        if (error != null) return stateResult = new StateResult(request == null ? -1 : request.revision(), Outcome.REJECTED, error);
        revision = request.revision();
        expiresAt = tick + request.leaseTicks();
        enabled = request.enabled();
        retreat = request.retreat();
        mode = request.mode();
        expired = false;
        return stateResult = new StateResult(revision, Outcome.ACCEPTED, "applied");
    }

    public ActionResult request(ActionRequest request, long tick) {
        return submit(request,tick,null).result();
    }
    public ActionResult request(ActionRequest request, long tick, String admissionError) {
        return submit(request,tick,admissionError).result();
    }
    /** 世代・再送を先に検証し、新規受理だけexecuteを返す。キャッシュしたACCEPTEDを再実行しない。 */
    public ActionAdmission submit(ActionRequest request, long tick, String admissionError) {
        expire(tick);
        String error = checkContext(request == null ? null : request.context());
        if (error == null && (request.actionId() == null || request.action() == null || request.sequence() < 0)) error = "invalid_action";
        if (error == null && actions.containsKey(request.actionId())) {
            Remembered old = actions.get(request.actionId());
            ActionResult result = old.request().equals(request) ? old.result()
                : new ActionResult(request.actionId(),request.sequence(),Outcome.REJECTED,"action_id_conflict");
            publish(request,result);
            return new ActionAdmission(result,false);
        }
        if (error == null && request.sequence() <= lastActionSequence) error = "old_sequence";
        if (error == null && (request.validForTicks() < 1 || request.validForTicks() > MAX_TRANSPORT_TICKS
                || !fresh(request.observedServerTick(), tick, request.validForTicks()))) error = "stale_request";
        boolean remember = error == null;
        boolean look = request != null && request.action() == Action.LOOK_AT;
        // 向きは移動制御の有効/退避状態と独立して適用する。
        if (error == null && !look && !enabled) error = "control_inactive";
        if (error == null && !look && retreat) error = "retreat_has_priority";
        if (error == null && admissionError != null) error = admissionError;
        ActionResult result = new ActionResult(request == null ? null : request.actionId(), request == null ? -1 : request.sequence(),
            error == null ? Outcome.ACCEPTED : Outcome.REJECTED, error == null ? "started" : error);
        // Invalid/foreign requests must not reserve IDs belonging to a future valid context.
        if (remember) {
            lastActionSequence = request.sequence();
            remember(result,request);
        }
        if (result.outcome() == Outcome.ACCEPTED) {
            if (look) {
                if (activeLook != null) completeAction(activeLook.actionId(),Outcome.INTERRUPTED,"superseded_by_new_look");
                activeLook=result;activeLookRequest=request;
            } else {
                completeAction(Outcome.INTERRUPTED,"superseded_by_new_action");
                activeAction=result;activeRequest=request;
            }
        }
        publish(request,result);
        return new ActionAdmission(result,result.outcome() == Outcome.ACCEPTED);
    }

    private void publish(ActionRequest request,ActionResult result) {
        if(request != null && request.action()==Action.LOOK_AT)lookResult=result;
        else actionResult=result;
    }
    public boolean hasActiveAction() { return activeAction != null; }
    public long lastActionSequence() { return lastActionSequence; }
    /** 移動と注視の完了を別に保存する。同時に完了しても片方の結果を上書きしない。 */
    public void completeAction(java.util.UUID id, Outcome outcome, String reason) {
        if (activeLook != null && activeLook.actionId().equals(id)) {
            lookResult=new ActionResult(id,activeLook.sequence(),outcome,reason);
            remember(lookResult,activeLookRequest);activeLook=null;activeLookRequest=null;
        } else if (activeAction != null && activeAction.actionId().equals(id)) completeAction(outcome,reason);
    }
    /** 身体の消失・ダウン時だけ、未完了の移動と注視を共に終端化する。 */
    public void interruptAll(String reason) {
        completeAction(Outcome.INTERRUPTED,reason);
        if(activeLook!=null)completeAction(activeLook.actionId(),Outcome.INTERRUPTED,reason);
    }
    public ActionResult lookResult() { return lookResult; }
    /** 既存32件台帳から注視結果を返す。同じsnapshot間隔中の次のLOOKで先の完了結果を失わない。 */
    public java.util.List<ActionResult> lookResults() {
        var results=new LinkedHashMap<java.util.UUID,ActionResult>();
        for(var remembered:actions.values())if(remembered.request().action()==Action.LOOK_AT)
            results.put(remembered.result().actionId(),remembered.result());
        // 期限・sequence等で台帳へ登録しない拒否も、待機元へ終端結果として返す。
        if(lookResult!=null && lookResult.actionId()!=null)results.put(lookResult.actionId(),lookResult);
        return java.util.List.copyOf(results.values());
    }
    public void completeAction(Outcome outcome, String reason) {
        if (activeAction != null) {
            actionResult = new ActionResult(activeAction.actionId(), activeAction.sequence(), outcome, reason);
            remember(actionResult,activeRequest);
            activeAction = null;
            activeRequest = null;
        }
    }

    private void remember(ActionResult result, ActionRequest request) {
        actions.put(result.actionId(), new Remembered(request,result));
        while (actions.size() > 32) actions.remove(actions.keySet().iterator().next());
    }

    public void local(Mode value, boolean isRetreating) {
        mode = value; enabled = true; retreat = isRetreating; expired = false; expiresAt = Long.MAX_VALUE;
    }
    public void pause() { enabled = false; retreat = false; mode = Mode.DIRECT; expiresAt = 0; }
    public void expire(long tick) {
        if (enabled && tick >= expiresAt) {
            enabled = retreat = false; expired = true;
            completeAction(Outcome.EXPIRED, "control_lease_expired");
        }
    }
    private String checkContext(Context value) {
        return context == null || !Objects.equals(context, value) ? "stale_context" : null;
    }
    private static boolean fresh(long observed, long now, long maxAge) {
        return observed >= 0 && observed <= now && now - observed <= maxAge;
    }
    public Context context() { return context; }
    public long revision() { return revision; }
    public boolean enabled() { return enabled; }
    public boolean retreat() { return retreat; }
    public boolean expired() { return expired; }
    public Mode mode() { return mode; }
    public StateResult stateResult() { return stateResult; }
    public ActionResult actionResult() { return actionResult; }
    public ActionResult actionResult(java.util.UUID id) {
        Remembered found = actions.get(id);
        return found == null ? null : found.result();
    }
}
