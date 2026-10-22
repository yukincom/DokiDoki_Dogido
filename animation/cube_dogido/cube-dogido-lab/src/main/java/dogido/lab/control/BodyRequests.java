package dogido.lab.control;

import dogido.lab.api.BodyProtocol.*;

/**
 * 外部のモデル要請の外形を検証する。値を補完して別の行動へ変換しない。
 * 結果はnull（外形成立）または閉じた理由。所有者/世代/鮮度、食料の現物は別の受付段階で照合する。
 */
public final class BodyRequests {
    private BodyRequests() {}
    public static String state(StateRequest request, boolean downed) {
        if (downed) return "body_downed";
        if (request == null || request.mode() == null) return null;
        if (request.mode() != Mode.DIRECT) return "model_control_requires_direct_mode";
        return request.retreat() ? "direct_mode_requires_chosen_destination" : null;
    }
    /** FOLLOWの観察は既存の呼び戻し・追従を上書きせず、ownerの行動範囲内だけで受け付ける。 */
    public static String inspection(boolean calling, boolean activeAction, Activity activity,
            double ownerDistance, double destinationOwnerDistance, double followDistance) {
        if (calling) return "call_has_priority";
        if (activeAction) return "body_action_busy";
        if (activity != Activity.HOLDING && activity != Activity.EXPLORING) return "body_activity_busy";
        if (ownerDistance > followDistance) return "distance_follow_has_priority";
        if (destinationOwnerDistance > followDistance) return "inspection_outside_follow_distance";
        return null;
    }
    /** owner接近は通常の見物半径を越えて選べるが、進行中の緊急追従やCALLを横取りしない。 */
    public static String ownerApproach(boolean calling, boolean activeAction, Activity activity,
            double ownerDistance, double emergencyDistance, MovePurpose purpose, boolean ownerWorking) {
        if (calling) return "call_has_priority";
        if (activeAction) return "body_action_busy";
        if (activity != Activity.HOLDING && activity != Activity.EXPLORING) return "body_activity_busy";
        if (ownerDistance > emergencyDistance) return "distance_follow_has_priority";
        return purpose == MovePurpose.OWNER_FACE && ownerWorking ? "owner_working" : null;
    }
    public static String action(ActionRequest request, Mode mode, boolean downed, Position current) {
        if (downed) return "body_downed";
        if (request == null || request.action() == null) return null;
        if (request.movePurpose() == null) return "invalid_move_purpose";
        if (request.movePurpose() != MovePurpose.INSPECT) {
            if (request.action() != Action.MOVE_TO) return "unexpected_move_purpose";
            if (mode != Mode.FOLLOW) return "owner_approach_requires_follow";
            if (request.destination() != null) return "unexpected_destination";
        }
        if (request.action() != Action.CALL && request.action() != Action.LOOK_AT && mode != Mode.DIRECT
                && !(mode == Mode.FOLLOW && request.action() == Action.MOVE_TO)) return "direct_mode_required";
        if (request.action() != Action.MOVE_TO && request.action() != Action.LOOK_AT && request.destination() != null) return "unexpected_destination";
        if (request.action() != Action.EAT && request.foodId() != null) return "unexpected_food_id";
        if (request.action() == Action.EAT && (request.foodId() == null || request.foodId().isBlank() || request.foodId().length() > 128)) return "invalid_food_id";
        if (request.action() == Action.LOOK_AT || request.action() == Action.MOVE_TO && request.movePurpose() == MovePurpose.INSPECT) {
            Position p = request.destination();
            if (p == null || !Double.isFinite(p.x()) || !Double.isFinite(p.y()) || !Double.isFinite(p.z())) return "invalid_destination";
            double x = p.x()-current.x(), y = p.y()-current.y(), z = p.z()-current.z();
            double limit = request.action() == Action.LOOK_AT ? 64 : 32;
            if (x*x+y*y+z*z > limit*limit) return "destination_too_far";
        }
        return null;
    }
}
