package dogido.lab.control;

import dogido.lab.api.BodyProtocol.*;
import java.util.UUID;
import net.minecraft.util.math.Vec3d;

/**
 * モデル/明示試験操作が指定した行き先とREST/WAITを保持する。危険度・HPによる行動選択はしない。
 * 毎tickは移動目標を返し、到着を一度だけ通知する。同じaction IDの再配送で移動を再開しない。
 */
public final class DirectedMotion {
    private UUID actionId;
    private Vec3d destination;
    private boolean resting, arrival;
    private int movingTicks;
    public void clear() { actionId = null; destination = null; resting = arrival = false; movingTicks = 0; }
    public void begin(UUID id, Action action, Vec3d target) {
        if (id.equals(actionId)) return;
        clear(); actionId = id;
        destination = action == Action.MOVE_TO ? target : null;
        resting = action == Action.REST;
    }
    public BodyController.Plan plan(Vec3d position) {
        if (destination != null) {
            movingTicks++;
            if (position.distanceTo(destination) <= .35) { destination = null; arrival = true; }
        }
        return new BodyController.Plan(destination == null ? position : destination,
            destination != null ? Activity.MOVING : resting ? Activity.RESTING : Activity.HOLDING,
            destination != null ? "requested_destination" : resting ? "resting" : "waiting_for_decision",true);
    }
    public boolean takeArrival() { boolean value = arrival; arrival = false; return value; }
    public boolean resting() { return resting; }
    public boolean moving() { return destination != null; }
    public int movingTicks() { return movingTicks; }
}
