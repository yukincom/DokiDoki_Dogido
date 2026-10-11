package dogido.fabric.horse_recall;

import net.fabricmc.api.ModInitializer;
import net.fabricmc.fabric.api.client.particle.v1.ParticleFactoryRegistry;
import net.fabricmc.fabric.api.particle.v1.FabricParticleTypes;
import net.minecraft.client.particle.BillboardParticle;
import net.minecraft.client.particle.SpriteProvider;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.particle.SimpleParticleType;
import net.minecraft.registry.Registries;
import net.minecraft.registry.Registry;
import net.minecraft.util.Identifier;

/**
 * 呼戻し専用の描画粒子。登録は起動時、生成・取消はclientの演出担当が所有する。
 * バニラのglitter素材を参照し、world照明を変更せず粒子自体だけを明るく描く。
 */
public final class HorseRecallParticles implements ModInitializer {
    public static final SimpleParticleType SPARK = FabricParticleTypes.simple();

    @Override public void onInitialize() {
        Registry.register(Registries.PARTICLE_TYPE, Identifier.of("dogido_client_adapter", "horse_recall"), SPARK);
    }

    public static void registerClient() {
        ParticleFactoryRegistry.getInstance().register(SPARK, provider -> (type, world, x, y, z, vx, vy, vz, random) ->
            new Spark(world, x, y, z, vx, vy, vz, provider));
    }

    public static final class Spark extends BillboardParticle {
        private final SpriteProvider sprites;
        private boolean burst;
        private double phase;

        Spark(ClientWorld world, double x, double y, double z, double vx, double vy, double vz, SpriteProvider sprites) {
            super(world, x, y, z, sprites.getFirst());
            this.sprites = sprites;
            this.collidesWithWorld = false;
            this.gravityStrength = 0;
            this.velocityMultiplier = 0.94F;
            this.setVelocity(vx, vy, vz);
            this.maxAge = 12;
        }

        public void configure(boolean yellow, boolean burst, int lifetime, double phase, float size) {
            this.setColor(1.0F, yellow ? 0.86F : 1.0F, yellow ? 0.14F : 1.0F);
            this.burst = burst;
            this.maxAge = lifetime;
            this.phase = phase;
            this.scale = size;
            this.alpha = HorseRecallVisuals.alpha(0, maxAge, phase, burst);
        }

        @Override public void tick() {
            super.tick();
            this.alpha = HorseRecallVisuals.alpha(age, maxAge, phase, burst);
            this.updateSprite(sprites);
        }

        @Override public RenderType getRenderType() { return RenderType.PARTICLE_ATLAS_TRANSLUCENT; }
        @Override public int getBrightness(float tickProgress) { return 15728880; }
    }
}
