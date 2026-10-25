package dogido.fabric.observation;

import com.google.gson.JsonObject;
import net.minecraft.entity.Entity;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.World;

/** 個体の現在の接水・水没・雨・溶岩への接触・接地・炎上を採る。乾燥や行動の意図は推定しない。 */
public record MobEnvironmentObservation(
    boolean touchingWater,
    boolean submergedInWater,
    boolean touchingWaterOrRain,
    boolean onGround,
    boolean onFire,
    boolean touchingRain,
    boolean touchingLava
) {
    /** 同じ個体の現在値だけを返す。雨と接水を別々に採り、水の中でも雨への接触を失わない。 */
    public static MobEnvironmentObservation capture(Entity entity) {
        return new MobEnvironmentObservation(
            entity.isTouchingWater(), entity.isSubmergedInWater(),
            entity.isTouchingWaterOrRain(), entity.isOnGround(), entity.isOnFire(),
            touchingRain(entity.getEntityPos(), entity.getBoundingBox(), entity.getEntityWorld()), entity.isInLava()
        );
    }

    /** MinecraftのEntityと同じ足元・身体上端の降雨判定。位置はblock単位で、積雪・濡れの残留とは別。 */
    public static boolean touchingRain(Vec3d position, Box bounds, World world) {
        return sampleRain(position.x, position.y, position.z, bounds.maxY,
            (x, y, z) -> world.hasRain(new BlockPos(x, y, z)));
    }
    @FunctionalInterface interface RainProbe { boolean at(int x, int y, int z); }
    static boolean sampleRain(double x, double feetY, double z, double topY, RainProbe rain) {
        int bx = (int)Math.floor(x), bz = (int)Math.floor(z);
        return rain.at(bx, (int)Math.floor(feetY), bz) || rain.at(bx, (int)Math.floor(topY), bz);
    }

    public JsonObject toJson() {
        JsonObject value = new JsonObject();
        value.addProperty("touching_water", touchingWater);
        value.addProperty("submerged_in_water", submergedInWater);
        value.addProperty("touching_water_or_rain", touchingWaterOrRain);
        value.addProperty("touching_rain", touchingRain);
        value.addProperty("touching_lava", touchingLava);
        value.addProperty("on_ground", onGround);
        value.addProperty("on_fire", onFire);
        return value;
    }
}
