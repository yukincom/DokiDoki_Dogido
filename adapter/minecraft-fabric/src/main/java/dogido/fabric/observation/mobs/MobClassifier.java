package dogido.fabric.observation.mobs;

import java.util.Set;
import java.util.UUID;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.mob.CreeperEntity;
import net.minecraft.entity.mob.MobEntity;
import net.minecraft.entity.mob.Monster;
import net.minecraft.entity.mob.PiglinEntity;
import net.minecraft.entity.mob.ShulkerEntity;
import net.minecraft.entity.mob.SpiderEntity;
import net.minecraft.entity.mob.ZombifiedPiglinEntity;
import net.minecraft.entity.passive.BeeEntity;
import net.minecraft.entity.passive.DolphinEntity;
import net.minecraft.entity.passive.FoxEntity;
import net.minecraft.entity.passive.GoatEntity;
import net.minecraft.entity.passive.IronGolemEntity;
import net.minecraft.entity.passive.LlamaEntity;
import net.minecraft.entity.passive.PandaEntity;
import net.minecraft.entity.passive.PolarBearEntity;
import net.minecraft.entity.passive.WolfEntity;
import net.minecraft.entity.player.PlayerEntity;

import static dogido.fabric.observation.MobIdentityObservation.entityTypeName;

public final class MobClassifier {
    public static final Set<String> NEUTRAL_MONSTER_TYPES = Set.of(
        "enderman", "spider", "cave_spider", "drowned", "piglin", "zombified_piglin"
    );

    // 一度でも殻を開いた（動き出した）シュルカー。閉じたままの個体は「ブロックのフリ」を尊重して黙る
    private final Set<UUID> awakenedShulkerIds = new java.util.HashSet<>();

    public MobDisposition classifyMobDisposition(ClientPlayerEntity player, LivingEntity entity) {
        String type = entityTypeName(entity);
        if (entity instanceof CreeperEntity creeper && creeper.isCharged()) {
            type = "charged_creeper";
        }
        if (entity instanceof ShulkerEntity shulker) {
            // 閉じたままのシュルカーは「ブロックのフリ」を尊重して気づかないフリをする。
            // 一度でも殻を開いたら（動き出したら）以後は脅威として扱う
            return new MobDisposition(type, false, isShulkerAwakened(shulker), null, "hostile");
        }
        if (entity instanceof Monster) {
            if (!NEUTRAL_MONSTER_TYPES.contains(type)) {
                return new MobDisposition(type, false, true, null, "hostile");
            }
            boolean hostileNow = isNeutralMobHostileToPlayer(player, entity);
            return new MobDisposition(type, !hostileNow, hostileNow, "neutral", neutralCautionReason(entity));
        }

        if (entity instanceof FoxEntity) {
            return new MobDisposition(type, true, false, "passive", null);
        }
        if (entity instanceof GoatEntity) {
            return new MobDisposition(type, true, false, "neutral", "charge");
        }
        if (entity instanceof BeeEntity
            || entity instanceof DolphinEntity
            || entity instanceof IronGolemEntity
            || entity instanceof LlamaEntity
            || entity instanceof PandaEntity
            || entity instanceof PolarBearEntity
            || entity instanceof WolfEntity) {
            boolean hostileNow = isNeutralMobHostileToPlayer(player, entity);
            return new MobDisposition(
                type,
                !hostileNow,
                hostileNow,
                hostileNow ? null : "neutral",
                neutralCautionReason(entity)
            );
        }

        return new MobDisposition(type, true, false, "passive", null);
    }

    private boolean isShulkerAwakened(ShulkerEntity shulker) {
        // openProgress は殻の開き具合（0.0=完全に閉じてブロックのフリ中）
        return observeShulkerAwakening(shulker.getUuid(), shulker.getOpenProgress(0.0f));
    }

    private boolean isNeutralMobHostileToPlayer(ClientPlayerEntity player, LivingEntity entity) {
        if (entity instanceof MobEntity mob) {
            LivingEntity target = mob.getTarget();
            if (target != null && target.getUuid().equals(player.getUuid())) {
                return true;
            }
        }
        if (entity instanceof ZombifiedPiglinEntity || entity instanceof PiglinEntity) {
            return entity instanceof MobEntity mob
                && mob.getTarget() != null
                && mob.getTarget().getUuid().equals(player.getUuid());
        }
        return false;
    }

    private String neutralCautionReason(LivingEntity entity) {
        if (entity instanceof BeeEntity) {
            return "swarm";
        }
        if (entity instanceof DolphinEntity || entity instanceof WolfEntity) {
            return "retaliates";
        }
        if (entity instanceof SpiderEntity) {
            return "darkness";
        }
        if (entity instanceof LlamaEntity) {
            return "spit";
        }
        if (entity instanceof PolarBearEntity) {
            return "protective";
        }
        if (entity instanceof net.minecraft.entity.mob.DrownedEntity) {
            return "water";
        }
        if (entity instanceof PandaEntity) {
            return "temper";
        }
        if (entity instanceof PiglinEntity) {
            return "gold";
        }
        if (entity instanceof ZombifiedPiglinEntity) {
            return "provoked_only";
        }
        if (entity instanceof IronGolemEntity) {
            return "village_guard";
        }
        if (entity instanceof GoatEntity) {
            return "charge";
        }
        return null;
    }

    public record MobDisposition(
        String type,
        boolean ambientEligible,
        boolean threatNow,
        String temperament,
        String cautionReason
    ) {
    }

    boolean observeShulkerAwakening(UUID id, float openProgress) {
        if (openProgress > 0.0f) this.awakenedShulkerIds.add(id);
        return this.awakenedShulkerIds.contains(id);
    }

    public void reset() { this.awakenedShulkerIds.clear(); }

    public static boolean isAmbientMobCandidate(Entity entity) {
        if (!(entity instanceof LivingEntity living) || !living.isAlive()) {
            return false;
        }
        if (entity instanceof PlayerEntity) {
            return false;
        }
        // HostileEntity 継承でないモンスター（ドラゴン・ガスト・ファントム・スライム・
        // シュルカー等）を友好モブ扱いしないよう Monster インターフェースで判定する
        if (entity instanceof Monster) {
            return NEUTRAL_MONSTER_TYPES.contains(entityTypeName(entity));
        }
        return true;
    }
}
