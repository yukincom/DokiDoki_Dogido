package dogido.fabric;

import com.google.gson.JsonObject;
import net.minecraft.entity.Entity;

/** Current measurements for one observed entity; no inferred survival or conversion. */
record MobEnvironmentObservation(
    boolean touchingWater,
    boolean submergedInWater,
    boolean touchingWaterOrRain,
    boolean onGround,
    boolean onFire
) {
    static MobEnvironmentObservation capture(Entity entity) {
        return new MobEnvironmentObservation(
            entity.isTouchingWater(), entity.isSubmergedInWater(),
            entity.isTouchingWaterOrRain(), entity.isOnGround(), entity.isOnFire()
        );
    }

    JsonObject toJson() {
        JsonObject value = new JsonObject();
        value.addProperty("touching_water", touchingWater);
        value.addProperty("submerged_in_water", submergedInWater);
        value.addProperty("touching_water_or_rain", touchingWaterOrRain);
        value.addProperty("on_ground", onGround);
        value.addProperty("on_fire", onFire);
        return value;
    }
}
