package dogido.fabric.screenshot;

import java.time.Instant;
import java.util.List;

/**
 * 同じ撮影器へ届く予約の順序だけを管理する。現在のsession/bodyは呼出前に既存の観測で照合する。
 * 明示注視後の予約中は通常予約を受けず、遅配された古い通常予約も完了後に復活させない。
 */
final class BodyViewRequestOrder {
    private Object context;
    private Instant lastLookIssued;
    boolean accept(BodyViewProtocol.Command command,boolean explicitPending) {
        Object key=List.of(command.sessionId(),command.bodyId(),command.epoch(),command.ownerId(),command.dimension());
        if(!key.equals(context)) { context=key;lastLookIssued=null; }
        if(command.afterLook()==null) {
            return !explicitPending && (lastLookIssued==null || command.issuedAt().isAfter(lastLookIssued));
        }
        if(lastLookIssued!=null && command.issuedAt().isBefore(lastLookIssued))return false;
        lastLookIssued=command.issuedAt();return true;
    }
}
