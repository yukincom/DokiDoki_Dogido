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
    private ActionResult actionResult, activeAction;
    private ActionRequest activeRequest;
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
        actionResult = activeAction = null;
        activeRequest = null;
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
            actionResult = old.request().equals(request) ? old.result()
                : new ActionResult(request.actionId(),request.sequence(),Outcome.REJECTED,"action_id_conflict");
            return new ActionAdmission(actionResult,false);
        }
        if (error == null && request.sequence() <= lastActionSequence) error = "old_sequence";
        if (error == null && (request.validForTicks() < 1 || request.validForTicks() > MAX_TRANSPORT_TICKS
                || !fresh(request.observedServerTick(), tick, request.validForTicks()))) error = "stale_request";
        boolean remember = error == null;
        if (error == null && !enabled) error = "control_inactive";
        if (error == null && retreat) error = "retreat_has_priority";
        if (error == null && admissionError != null) error = admissionError;
        ActionResult result = new ActionResult(request == null ? null : request.actionId(), request == null ? -1 : request.sequence(),
            error == null ? Outcome.ACCEPTED : Outcome.REJECTED, error == null ? "started" : error);
        // Invalid/foreign requests must not reserve IDs belonging to a future valid context.
        if (remember) {
            lastActionSequence = request.sequence();
            remember(result,request);
        }
        if (result.outcome() == Outcome.ACCEPTED) {
            completeAction(Outcome.INTERRUPTED,"superseded_by_new_action");
            activeAction = result;
            activeRequest = request;
        }
        actionResult = result;
        return new ActionAdmission(result,result.outcome() == Outcome.ACCEPTED);
    }

    public boolean hasActiveAction() { return activeAction != null; }
    /** 先の行動の終了通知で、後から受け付けた行動を完了させない。 */
    public void completeAction(java.util.UUID id, Outcome outcome, String reason) {
        if (activeAction != null && activeAction.actionId().equals(id)) completeAction(outcome,reason);
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
}
