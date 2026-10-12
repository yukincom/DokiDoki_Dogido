package dogido.fabric.horse_recall;

import java.util.UUID;
import net.minecraft.entity.Entity;
import net.minecraft.entity.Leashable;
import net.minecraft.entity.EquipmentSlot;
import net.minecraft.entity.mob.MobEntity;
import net.minecraft.entity.passive.AbstractHorseEntity;
import net.minecraft.entity.passive.HappyGhastEntity;
import net.minecraft.entity.passive.TameableEntity;
import net.minecraft.registry.Registries;
import net.minecraft.registry.tag.ItemTags;
import net.minecraft.sound.SoundEvent;
import net.minecraft.sound.SoundEvents;

/** 種類ごとのMinecraft APIを既存の呼び戻しへつなぐ。保存・移動・候補選択は担当しない。 */
final class HorseRecallMounts {
    private HorseRecallMounts() {}

    static HorseRecallChecks.Kind kind(Entity entity) {
        return entity == null ? null : HorseRecallChecks.Kind.fromEntityId(Registries.ENTITY_TYPE.getId(entity.getType()).toString());
    }

    /** サーバーで毎回取得する。鞍や名札を外した個体、他人へ所有変更した個体は次の再照合で失効する。 */
    static HorseRecallChecks.Target target(Entity entity) {
        var kind = kind(entity);
        if (kind == null || !(entity instanceof MobEntity mob)) return null;
        UUID owner = null;
        boolean tamed = false;
        if (mob instanceof AbstractHorseEntity horse) {
            tamed = horse.isTame();
            if (horse.getOwnerReference() != null) owner = horse.getOwnerReference().getUuid();
        } else if (mob instanceof TameableEntity tameable) {
            tamed = tameable.isTamed();
            if (tameable.getOwnerReference() != null) owner = tameable.getOwnerReference().getUuid();
        }
        boolean equipped = kind.flying() ? mob.getEquippedStack(EquipmentSlot.BODY).isIn(ItemTags.HARNESSES)
            : mob.hasSaddleEquipped();
        return new HorseRecallChecks.Target(kind, mob.isAlive() && !mob.isRemoved(), tamed, owner,
            mob.getCustomName() == null ? null : mob.getCustomName().getString(), !mob.isBaby(), equipped,
            mob.getEntityWorld().getRegistryKey().getValue().toString(), mob.hasPassengers(), mob.hasVehicle(),
            mob.isLeashed() || mob instanceof HappyGhastEntity && !Leashable.collectLeashablesHeldBy(mob).isEmpty());
    }

    /** 到着をclientが観測した瞬間の音。馬の嘶きを他種へ流用しない。 */
    static SoundEvent sound(HorseRecallChecks.Kind kind) {
        return switch (kind) {
            case HORSE -> SoundEvents.ENTITY_HORSE_AMBIENT;
            case DONKEY -> SoundEvents.ENTITY_DONKEY_AMBIENT;
            case MULE -> SoundEvents.ENTITY_MULE_AMBIENT;
            case SKELETON_HORSE -> SoundEvents.ENTITY_SKELETON_HORSE_AMBIENT;
            case ZOMBIE_HORSE -> SoundEvents.ENTITY_ZOMBIE_HORSE_AMBIENT;
            case CAMEL -> SoundEvents.ENTITY_CAMEL_AMBIENT;
            case CAMEL_HUSK -> SoundEvents.ENTITY_CAMEL_HUSK_AMBIENT;
            case PIG -> SoundEvents.ENTITY_PIG_AMBIENT;
            case STRIDER -> SoundEvents.ENTITY_STRIDER_AMBIENT;
            case HAPPY_GHAST -> SoundEvents.ENTITY_HAPPY_GHAST_AMBIENT;
            case NAUTILUS -> SoundEvents.ENTITY_NAUTILUS_AMBIENT;
            case ZOMBIE_NAUTILUS -> SoundEvents.ENTITY_ZOMBIE_NAUTILUS_AMBIENT;
            case LLAMA, TRADER_LLAMA -> SoundEvents.ENTITY_LLAMA_AMBIENT;
        };
    }
}
