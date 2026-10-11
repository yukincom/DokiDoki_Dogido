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
    public static String action(ActionRequest request, Mode mode, boolean downed, Position current) {
        if (downed) return "body_downed";
        if (request == null || request.action() == null) return null;
        if (request.action() != Action.CALL && mode != Mode.DIRECT) return "direct_mode_required";
        if (request.action() != Action.MOVE_TO && request.destination() != null) return "unexpected_destination";
        if (request.action() != Action.EAT && request.foodId() != null) return "unexpected_food_id";
        if (request.action() == Action.EAT && (request.foodId() == null || request.foodId().isBlank() || request.foodId().length() > 128)) return "invalid_food_id";
        if (request.action() == Action.MOVE_TO) {
            Position p = request.destination();
            if (p == null || !Double.isFinite(p.x()) || !Double.isFinite(p.y()) || !Double.isFinite(p.z())) return "invalid_destination";
            double x = p.x()-current.x(), y = p.y()-current.y(), z = p.z()-current.z();
            if (x*x+y*y+z*z > 32*32) return "destination_too_far";
        }
        return null;
    }
}
