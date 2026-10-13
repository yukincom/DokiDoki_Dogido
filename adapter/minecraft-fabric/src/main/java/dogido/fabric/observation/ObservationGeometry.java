package dogido.fabric.observation;

import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.util.math.Vec3d;

public final class ObservationGeometry {
    private ObservationGeometry() {}

    public static String classifyHorizontal(ClientPlayerEntity player, Vec3d targetPos) {
        return classifyHorizontal(ObservationOrigin.player(player),targetPos);
    }
    /** 同じ観測の位置とyawを使う。方向計算はplayer/身体で共有し、向きを混ぜない。 */
    public static String classifyHorizontal(ObservationOrigin origin, Vec3d targetPos) {
        Vec3d toTarget = new Vec3d(targetPos.x-origin.position().x,0.0,targetPos.z-origin.position().z);
        if (toTarget.lengthSquared() < 0.0001) {
            return "front";
        }
        Vec3d normalized = toTarget.normalize();

        double yawRadians = Math.toRadians(origin.yaw());
        double forwardX = -Math.sin(yawRadians);
        double forwardZ = Math.cos(yawRadians);

        double dot = forwardX * normalized.x + forwardZ * normalized.z;
        double cross = forwardZ * normalized.x - forwardX * normalized.z;
        double angle = Math.toDegrees(Math.atan2(cross, dot));

        if (angle >= -35.0 && angle < 35.0) {
            return "front";
        }
        if (angle >= 35.0 && angle < 80.0) {
            return "front_left";
        }
        if (angle >= 80.0 && angle < 125.0) {
            return "left";
        }
        if (angle >= 125.0 && angle < 155.0) {
            return "back_left";
        }
        if (angle >= 155.0 || angle < -155.0) {
            return "back";
        }
        if (angle >= -155.0 && angle < -125.0) {
            return "back_right";
        }
        if (angle >= -125.0 && angle < -80.0) {
            return "right";
        }
        return "front_right";
    }

    public static String classifyCardinal(ClientPlayerEntity player, Vec3d targetPos) {
        return classifyCardinal(ObservationOrigin.player(player),targetPos);
    }
    public static String classifyCardinal(ObservationOrigin origin, Vec3d targetPos) {
        double deltaX = targetPos.x-origin.position().x;
        double deltaZ = targetPos.z-origin.position().z;
        if (deltaX * deltaX + deltaZ * deltaZ < 0.0001) {
            return "south";
        }

        // Minecraft Java: 北=-Z、南=+Z、東=+X、西=-X。
        double angle = Math.toDegrees(Math.atan2(deltaX, -deltaZ));
        if (angle >= -22.5 && angle < 22.5) {
            return "north";
        }
        if (angle >= 22.5 && angle < 67.5) {
            return "northeast";
        }
        if (angle >= 67.5 && angle < 112.5) {
            return "east";
        }
        if (angle >= 112.5 && angle < 157.5) {
            return "southeast";
        }
        if (angle >= 157.5 || angle < -157.5) {
            return "south";
        }
        if (angle >= -157.5 && angle < -112.5) {
            return "southwest";
        }
        if (angle >= -112.5 && angle < -67.5) {
            return "west";
        }
        return "northwest";
    }

    public static String classifyVertical(ClientPlayerEntity player, Vec3d targetPos) {
        return classifyVertical(ObservationOrigin.player(player),targetPos);
    }
    public static String classifyVertical(ObservationOrigin origin, Vec3d targetPos) {
        double deltaY = targetPos.y-origin.position().y;
        if (deltaY > 1.5) {
            return "above";
        }
        if (deltaY < -1.5) {
            return "below";
        }
        return "same";
    }

    public static String bucketDistance(double distance) {
        if (distance <= 1.5) {
            return "touching";
        }
        if (distance <= 4.0) {
            return "very_close";
        }
        if (distance <= 8.0) {
            return "close";
        }
        if (distance <= 16.0) {
            return "mid";
        }
        return "far";
    }

    public static int distanceBandRank(String band) {
        return switch (band) {
            case "touching" -> 0;
            case "very_close" -> 1;
            case "close" -> 2;
            case "mid" -> 3;
            case "far" -> 4;
            default -> 99;
        };
    }
}
