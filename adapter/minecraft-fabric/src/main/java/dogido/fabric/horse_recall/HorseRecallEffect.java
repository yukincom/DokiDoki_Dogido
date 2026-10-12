package dogido.fabric.horse_recall;

import java.util.ArrayList;
import java.util.List;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.particle.Particle;
import net.minecraft.entity.Entity;
import net.minecraft.entity.passive.AbstractHorseEntity;
import net.minecraft.sound.SoundCategory;
import org.slf4j.Logger;

/**
 * client threadだけで進む予兆・到着演出。world移動や成功ACKを発生させない。
 * 到着後のUUIDと位置（馬系では立ち上がり姿勢も）を見てから同じtickで音と散開を一度だけ開始する。
 */
final class HorseRecallEffect {
    final HorseRecallLifecycle lifecycle;
    private final HorseRecallProtocol.Command command;
    private final HorseRecallLanding.Point point;
    private final Logger logger;
    private final List<Particle> particles = new ArrayList<>();
    private int arrivalWaitTicks;
    private int burstTicks;
    private final double widthScale;
    private final double heightScale;

    HorseRecallEffect(HorseRecallProtocol.Command command, HorseRecallLanding.Point point,
            HorseRecallLifecycle lifecycle, Logger logger, float width, float height) {
        this.command = command;
        this.point = point;
        this.lifecycle = lifecycle;
        this.logger = logger;
        widthScale = Math.max(1, width / 1.3965);
        heightScale = Math.max(1, height / 1.6);
    }

    /** trueで描画担当を解放できる。完了は描画を要求し終えた意味で、実画面/実スピーカーACKではない。 */
    boolean tick(MinecraftClient client) {
        particles.removeIf(particle -> !particle.isAlive());
        switch (lifecycle.stage()) {
            case CANCELLED -> { cancel(); return true; }
            case FINISHED -> { return true; }
            case PREVIEW -> preview(client);
            case MOVED -> {
                Entity entity = client.world.getEntity(command.entityId());
                var kind = HorseRecallMounts.kind(entity);
                if (kind != null && entity.isAlive()
                        && entity.squaredDistanceTo(point.x(), point.y(), point.z()) < 1.0
                        && (!kind.rears() || entity instanceof AbstractHorseEntity horse && horse.getAngryAnimationProgress(1.0F) >= 0.8F)) {
                    if (lifecycle.startBurst()) {
                        cancel();
                        if (!entity.isSilent()) client.world.playSoundClient(point.x(), point.y(), point.z(),
                            HorseRecallMounts.sound(kind), SoundCategory.NEUTRAL, 0.8F, 1.0F, false);
                        burst(client);
                        logger.info("Dogido horse presentation: command_id={} stage=burst_started particles={}",
                            command.commandId(), particles.size());
                    }
                } else if (++arrivalWaitTicks >= 40) {
                    lifecycle.cancel();
                    cancel();
                    logger.info("Dogido horse presentation: command_id={} stage=cancelled detail=arrival_not_observed",
                        command.commandId());
                    return true;
                }
            }
            case BURST -> {
                if (++burstTicks >= 32) {
                    lifecycle.finish();
                    cancel();
                    logger.info("Dogido horse presentation: command_id={} stage=finished", command.commandId());
                    return true;
                }
            }
            default -> { }
        }
        return false;
    }

    void cancel() {
        particles.forEach(Particle::markDead);
        particles.clear();
    }

    private void preview(MinecraftClient client) {
        var random = client.world.random;
        for (int i = 0; i < 10; i++) {
            boolean yellow = i >= 3;
            double angle = random.nextDouble() * Math.PI * 2;
            double radius = (yellow ? 0.6 + random.nextDouble() * 0.35 : random.nextDouble() * 0.22) * widthScale;
            spawn(client, point.x() + Math.cos(angle) * radius, point.y() + (0.3 + random.nextDouble() * 1.8) * heightScale,
                point.z() + Math.sin(angle) * radius, 0, 0.005, 0, yellow, false, 10, yellow ? 0.20F : 0.32F);
        }
    }

    private void burst(MinecraftClient client) {
        var random = client.world.random;
        for (int i = 0; i < 72; i++) {
            double angle = random.nextDouble() * Math.PI * 2;
            double speed = 0.05 + random.nextDouble() * 0.13;
            boolean yellow = i % 3 != 0;
            double radius = (yellow ? 0.65 : 0.18) * widthScale;
            spawn(client, point.x() + Math.cos(angle) * radius, point.y() + (0.4 + random.nextDouble() * 1.8) * heightScale,
                point.z() + Math.sin(angle) * radius, Math.cos(angle) * speed,
                0.01 + random.nextDouble() * 0.06, Math.sin(angle) * speed, yellow, true,
                18 + random.nextInt(13), 0.12F + random.nextFloat() * 0.09F);
        }
    }

    private void spawn(MinecraftClient client, double x, double y, double z, double vx, double vy, double vz,
            boolean yellow, boolean burst, int lifetime, float size) {
        Particle particle = client.particleManager.addParticle(HorseRecallParticles.SPARK, x, y, z, vx, vy, vz);
        if (particle instanceof HorseRecallParticles.Spark spark) {
            spark.configure(yellow, burst, lifetime, client.world.random.nextDouble() * Math.PI * 2, size);
            particles.add(spark);
        }
    }
}
