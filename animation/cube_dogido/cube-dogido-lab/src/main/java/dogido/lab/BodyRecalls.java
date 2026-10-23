package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.WeakHashMap;
import net.minecraft.server.network.ServerPlayerEntity;

/** 接続中の実ownerによる明示CALLだけを既存の呼び寄せへ接続する。再配送は同じ実行の結果を返す。 */
final class BodyRecalls {
    private record Entry(RecallRequest request, RecallReceipt receipt, long deadline) {}
    private static final class State {
        final LinkedHashMap<UUID,Entry> entries = new LinkedHashMap<>();
        UUID latest;
    }
    private static final Map<ServerPlayerEntity,State> STATES = new WeakHashMap<>();
    private BodyRecalls() {}
    static RecallReceipt submit(ServerPlayerEntity player, RecallRequest request) {
        if (request == null || request.ownerId() == null || request.connectionId() == null || request.actionId() == null) return null;
        if (!player.getUuid().equals(request.ownerId())) return null;
        State state = STATES.computeIfAbsent(player,p -> new State());
        Entry previous = state.entries.get(request.actionId());
        if (previous != null) {
            // 今回の応答には元IDの結果を返す。poll先を古いIDへ戻さず、別の進行中CALLを保持する。
            if (previous.request().equals(request)) return refresh(player,state,request.actionId());
            return new RecallReceipt(request.ownerId(),request.connectionId(),request.actionId(),request.dimension(),
                previous.receipt().resolvedContext(),new ActionResult(request.actionId(),previous.receipt().result().sequence(),Outcome.REJECTED,"action_id_conflict"));
        }
        long tick = player.getEntityWorld().getServer().getTicks();
        String dimension = player.getEntityWorld().getRegistryKey().getValue().toString();
        String problem = !Objects.equals(dimension,request.dimension()) ? "stale_dimension"
            : request.validForTicks() < 1 || request.validForTicks() > 20 || request.observedServerTick() < 0
                || request.observedServerTick() > tick || tick-request.observedServerTick() > request.validForTicks() ? "stale_request"
            : !player.isAlive() || player.isSpectator() ? "owner_unavailable" : null;
        RecallReceipt active = current(player);
        if (problem == null && active != null && active.result().outcome() == Outcome.ACCEPTED) problem = "body_action_pending";
        ActionResult result = problem == null ? BodyManager.recall(player,request.actionId())
            : new ActionResult(request.actionId(),-1,Outcome.REJECTED,problem);
        // 生成・所有確認に至らなかった失敗も元の命令IDへ戻す。
        if (!request.actionId().equals(result.actionId())) result = new ActionResult(request.actionId(),result.sequence(),result.outcome(),result.reason());
        DogidoEntity body = BodyManager.body(player);
        Context resolved = body == null ? null : body.snapshot().context();
        RecallReceipt receipt = new RecallReceipt(request.ownerId(),request.connectionId(),request.actionId(),request.dimension(),resolved,result);
        state.entries.put(request.actionId(),new Entry(request,receipt,tick+700));
        // 新しい拒否receiptで、進行中CALLの完了監視を置き換えない。
        if (result.outcome() == Outcome.ACCEPTED || active == null || active.result().outcome() != Outcome.ACCEPTED)
            state.latest = request.actionId();
        while (state.entries.size() > 32) {
            UUID remove=state.entries.keySet().stream().filter(id -> !id.equals(state.latest)).findFirst().orElseThrow();
            state.entries.remove(remove);
        }
        return receipt;
    }
    static RecallReceipt current(ServerPlayerEntity player) {
        State state = STATES.get(player);
        return state == null ? null : refresh(player,state,state.latest);
    }
    private static RecallReceipt refresh(ServerPlayerEntity player, State state, UUID actionId) {
        Entry entry = state.entries.get(actionId);
        if (entry == null) return null;
        RecallReceipt receipt = entry.receipt();
        if (receipt.result().outcome() != Outcome.ACCEPTED) return receipt;
        DogidoEntity body = BodyManager.body(player);
        Context context = receipt.resolvedContext();
        ActionResult result = null;
        if (!Objects.equals(receipt.dimension(),player.getEntityWorld().getRegistryKey().getValue().toString()))
            result = new ActionResult(receipt.actionId(),receipt.result().sequence(),Outcome.INTERRUPTED,"stale_dimension");
        else if (body == null || context == null || !body.getUuid().equals(context.bodyId()))
            result = new ActionResult(receipt.actionId(),receipt.result().sequence(),Outcome.INTERRUPTED,"body_unavailable");
        else {
            // CALL中の安全な転送では同じ個体のepochが変わる。実owner・実bodyから再取得する。
            result = body.recalledResult(player,receipt.actionId());
            context = body.snapshot().context();
        }
        if ((result == null || result.outcome() == Outcome.ACCEPTED) && player.getEntityWorld().getServer().getTicks() >= entry.deadline())
            result = new ActionResult(receipt.actionId(),receipt.result().sequence(),Outcome.EXPIRED,"result_timeout");
        if (result != null) {
            receipt = new RecallReceipt(receipt.ownerId(),receipt.connectionId(),receipt.actionId(),receipt.dimension(),context,result);
            state.entries.put(receipt.actionId(),new Entry(entry.request(),receipt,entry.deadline()));
        }
        return receipt;
    }
}
