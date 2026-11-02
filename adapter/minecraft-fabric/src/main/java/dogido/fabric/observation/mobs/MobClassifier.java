package dogido.fabric.observation.mobs;

import java.util.Map;
import java.util.Set;
import java.util.function.BiPredicate;
import java.util.UUID;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.entity.Entity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.mob.CreeperEntity;
import net.minecraft.entity.mob.PiglinEntity;
import net.minecraft.entity.mob.ShulkerEntity;
import net.minecraft.entity.mob.SpiderEntity;
import net.minecraft.entity.mob.ZombifiedPiglinEntity;
import net.minecraft.entity.passive.BeeEntity;
import net.minecraft.entity.passive.DolphinEntity;
import net.minecraft.entity.passive.GoatEntity;
import net.minecraft.entity.passive.IronGolemEntity;
import net.minecraft.entity.passive.LlamaEntity;
import net.minecraft.entity.passive.PandaEntity;
import net.minecraft.entity.passive.PolarBearEntity;
import net.minecraft.entity.passive.WolfEntity;
import net.minecraft.entity.player.PlayerEntity;

import static dogido.fabric.observation.MobIdentityObservation.entityTypeName;

public final class MobClassifier {
    // Nettyの発音通知も読むため、対象者とその個体一覧を一緒に公開する。
    private record Opponents(UUID player, Map<UUID, String> evidence) {}
    private volatile Opponents current = new Opponents(null, Map.of());
    private final BiPredicate<UUID,UUID> opponentLookup;
    public MobClassifier() {
        this.opponentLookup = (receiver, mob) -> {
            var snapshot = this.current;
            return receiver != null && receiver.equals(snapshot.player()) && snapshot.evidence().containsKey(mob);
        };
    }
    // 身体視点や明示観察の位置・視線の履歴は独立させ、交戦相手だけは本人の観測を使う。
    public MobClassifier(BiPredicate<UUID,UUID> lookup) { this.opponentLookup = lookup; }
    public boolean hasOpponent(UUID receiverId, UUID mobId) {
        return mobId != null && this.opponentLookup.test(receiverId, mobId);
    }
    void publishOpponents(UUID owner, Map<UUID,String> evidence) {
        this.current = new Opponents(owner, Map.copyOf(evidence));
    }
    Map<UUID,String> opponentEvidence() { return current.evidence(); }

    // 一度でも殻を開いた（動き出した）シュルカー。閉じたままの個体は「ブロックのフリ」を尊重して黙る
    private final Set<UUID> awakenedShulkerIds = new java.util.HashSet<>();

    public MobDisposition classifyMobDisposition(ClientPlayerEntity player, LivingEntity entity) {
        return classifyMobDisposition(player.getUuid(),entity);
    }
    /** 敵対判断の対象UUIDを明示する。身体視認でもownerへの敵対規則を維持し、履歴はplayer戦闘と共有しない。 */
    public MobDisposition classifyMobDisposition(UUID receiverId, LivingEntity entity) {
        String type = entityTypeName(entity);
        if (entity instanceof CreeperEntity creeper && creeper.isCharged()) {
            type = "charged_creeper";
        }
        boolean concealed = entity instanceof ShulkerEntity shulker && !isShulkerAwakened(shulker);
        boolean neutral = entity instanceof GoatEntity || ThreatObserver.isCombatMob(entity);
        return disposition(receiverId, entity.getUuid(), type, concealed,
            neutral ? "neutral" : "passive", neutralCautionReason(entity));
    }

    MobDisposition disposition(UUID receiver, UUID source, String type, boolean concealed, String temperament, String caution) {
        // 非交戦の閉じたシュルカーだけは従来の視認方針を保つ。殻が開いても敵対の根拠にはしない。
        return hasOpponent(receiver, source) ? new MobDisposition(type, false, true, null, "hostile")
            : new MobDisposition(type, !concealed, false, temperament, caution);
    }

    private boolean isShulkerAwakened(ShulkerEntity shulker) {
        // openProgress は殻の開き具合（0.0=完全に閉じてブロックのフリ中）
        return observeShulkerAwakening(shulker.getUuid(), shulker.getOpenProgress(0.0f));
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
        // ここは走査候補だけ。本人との関係はclassifyMobDispositionで個体ごとに決める。
        return true;
    }
}
