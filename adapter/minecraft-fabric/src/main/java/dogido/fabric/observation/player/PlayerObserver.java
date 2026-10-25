package dogido.fabric.observation.player;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dogido.fabric.observation.MobEnvironmentObservation;
import java.util.LinkedHashMap;
import java.util.Map;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.component.DataComponentTypes;
import net.minecraft.component.type.AttributeModifiersComponent;
import net.minecraft.entity.Entity;
import net.minecraft.entity.EquipmentSlot;
import net.minecraft.entity.LivingEntity;
import net.minecraft.entity.attribute.EntityAttributes;
import net.minecraft.entity.passive.AbstractHorseEntity;
import net.minecraft.entity.passive.CamelEntity;
import net.minecraft.entity.vehicle.AbstractBoatEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.registry.Registries;
import net.minecraft.registry.tag.ItemTags;
import net.minecraft.util.Identifier;
import net.minecraft.util.PlayerInput;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.hit.EntityHitResult;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;

import static dogido.fabric.observation.MobIdentityObservation.mobIdentity;
import static dogido.fabric.observation.ObservationNumbers.round;
import static dogido.fabric.observation.player.ItemIdentity.itemId;

public final class PlayerObserver {
    // 乗り物の微小な同期揺れを「移動中」と誤認しないための水平速度閾値。
    // 単位は block/tick の二乗。実際の歩行・航行速度より十分小さい。
    private static final double VEHICLE_MOVING_SPEED_SQUARED = 0.0001;

    public boolean respawnPointObserved() { return this.respawnPointObserved; }

    private boolean wasSleeping = false;
    private boolean respawnPointObserved = false;
    private BlockPos observedRespawnPos = null;
    private String observedRespawnDimension = null;
    private final String playerNameOverride;

    public PlayerObserver(String playerNameOverride) {
        this.playerNameOverride = playerNameOverride;
    }

    public void reset() {
        this.wasSleeping = false;
        this.respawnPointObserved = false;
        this.observedRespawnPos = null;
        this.observedRespawnDimension = null;
    }

    public void observeSleep(ClientPlayerEntity player, ClientWorld world, java.util.function.IntSupplier nearbyBedCount) {
        boolean sleepingNow = player.isSleeping();
        if (sleepingNow && !this.wasSleeping && nearbyBedCount.getAsInt() > 0) {
            this.respawnPointObserved = true;
            this.observedRespawnPos = player.getBlockPos().toImmutable();
            this.observedRespawnDimension = world.getRegistryKey().getValue().toString();
        }
        this.wasSleeping = sleepingNow;
    }

    public JsonObject buildPlayer(ClientPlayerEntity player, ClientWorld world) {
        JsonObject json = new JsonObject();
        json.addProperty("name", resolvePlayerName(player));
        json.add("environment", MobEnvironmentObservation.capture(player).toJson());
        // 同じsnapshotの実移動。距離はblock、垂直速度はblock/tick。着地先・被害量・落下原因は推測しない。
        JsonObject motion = new JsonObject();
        motion.addProperty("fall_distance", player.fallDistance);
        motion.addProperty("vertical_velocity", player.getVelocity().y);
        motion.addProperty("climbing", player.isClimbing());
        motion.addProperty("gliding", player.isGliding());
        motion.addProperty("flying", player.getAbilities().flying);
        json.add("motion", motion);
        json.addProperty("invulnerable", player.getAbilities().invulnerable || player.isSpectator());

        JsonObject position = new JsonObject();
        position.addProperty("x", round(player.getX()));
        position.addProperty("y", round(player.getY()));
        position.addProperty("z", round(player.getZ()));
        json.add("position", position);

        json.addProperty("yaw", round(player.getYaw()));
        json.addProperty("pitch", round(player.getPitch()));
        json.addProperty("health", round(player.getHealth()));
        json.addProperty("hunger", player.getHungerManager().getFoodLevel());
        json.addProperty("dimension", world.getRegistryKey().getValue().toString());

        ItemStack held = player.getMainHandStack();
        json.addProperty(
            "held_item",
            held.isEmpty() ? "minecraft:air" : Registries.ITEM.getId(held.getItem()).toString()
        );
        MinecraftClient client = MinecraftClient.getInstance();
        json.addProperty(
            "block_breaking_active",
            blockBreakingActive(client)
        );
        json.add("hotbar", buildHotbar(player));
        JsonObject vehicle = buildVehicleState(player);
        if (vehicle != null) {
            // 未乗車時はキーごと省略する。「乗っていない」をLLM材料にしない。
            json.add("vehicle", vehicle);
        }
        JsonArray activeEffects = new JsonArray();
        for (net.minecraft.entity.effect.StatusEffectInstance effect : player.getStatusEffects()) {
            Identifier effectId = effect.getEffectType().getKey().map(key -> key.getValue()).orElse(null);
            if (effectId == null) {
                continue;
            }
            activeEffects.add(effectId.getPath());
        }
        json.add("active_status_effects", activeEffects);
        return json;
    }

    /** 通常観測と身体の立ち位置で共有する現在の破壊操作。完了済みの採掘履歴や持ち物から推測しない。 */
    public static boolean blockBreakingActive(MinecraftClient client) {
        return client != null && client.interactionManager != null && client.interactionManager.isBreakingBlock();
    }

    private JsonObject buildVehicleState(ClientPlayerEntity player) {
        Entity vehicle = player.getVehicle();
        if (vehicle == null) {
            return null;
        }

        boolean controlling = vehicle.getControllingPassenger() == player;
        Vec3d velocity = vehicle.getVelocity();
        double horizontalSpeedSquared = (velocity.x * velocity.x) + (velocity.z * velocity.z);
        boolean moving = horizontalSpeedSquared >= VEHICLE_MOVING_SPEED_SQUARED;
        PlayerInput input = player.getLastPlayerInput();
        boolean directionalInput = input.forward() || input.backward() || input.left() || input.right();

        String activity = "riding";
        if (
            controlling
                && vehicle instanceof AbstractBoatEntity boat
                && (boat.isPaddleMoving(0) || boat.isPaddleMoving(1))
        ) {
            // 船・いかだだけは、パドル実測があるときに限り「漕いでいる」。
            activity = "rowing";
        } else if (controlling && vehicle instanceof CamelEntity camel && camel.isDashing()) {
            activity = "dashing";
        } else if (
            controlling
                && moving
                && directionalInput
                && vehicle instanceof AbstractHorseEntity
        ) {
            // 落下・押し流し・ノックバックだけでは「走っている」にしない。
            activity = "running";
        } else if (moving) {
            activity = "moving";
        }

        JsonObject json = new JsonObject();
        json.addProperty("vehicle_id", Registries.ENTITY_TYPE.getId(vehicle.getType()).toString());
        json.addProperty("activity", activity);
        json.addProperty("controlling", controlling);
        return json;
    }

    public JsonObject buildInventory(ClientPlayerEntity player) {
        Map<String, Integer> counts = new LinkedHashMap<>();
        int size = player.getInventory().size();
        for (int slot = 0; slot < size; slot += 1) {
            ItemStack stack = player.getInventory().getStack(slot);
            if (stack.isEmpty()) {
                continue;
            }
            String key = Registries.ITEM.getId(stack.getItem()).getPath();
            counts.merge(key, stack.getCount(), Integer::sum);
        }

        JsonObject json = new JsonObject();
        for (Map.Entry<String, Integer> entry : counts.entrySet()) {
            json.addProperty(entry.getKey(), entry.getValue());
        }
        return json;
    }

    private JsonObject buildHotbar(ClientPlayerEntity player) {
        JsonObject hotbar = new JsonObject();
        hotbar.addProperty("selected_slot", player.getInventory().getSelectedSlot());
        JsonArray slots = new JsonArray();
        for (int slot = 0; slot < net.minecraft.entity.player.PlayerInventory.getHotbarSize(); slot += 1) {
            ItemStack stack = player.getInventory().getStack(slot);
            JsonObject row = new JsonObject();
            row.addProperty("slot", slot);
            if (stack.isEmpty()) {
                row.addProperty("count", 0);
                row.addProperty("damage", 0);
                row.addProperty("max_damage", 0);
                row.addProperty("weapon_kind", "empty");
            } else {
                row.addProperty("item_id", itemId(stack));
                row.addProperty("count", stack.getCount());
                row.addProperty("damage", stack.getDamage());
                row.addProperty("max_damage", stack.getMaxDamage());
                row.addProperty("attack_damage", round(attackDamage(player, stack)));
                row.addProperty("weapon_kind", weaponKind(stack));
            }
            slots.add(row);
        }
        hotbar.add("slots", slots);
        return hotbar;
    }

    private double attackDamage(ClientPlayerEntity player, ItemStack stack) {
        AttributeModifiersComponent modifiers = stack.getOrDefault(
            DataComponentTypes.ATTRIBUTE_MODIFIERS,
            AttributeModifiersComponent.DEFAULT
        );
        return modifiers.applyOperations(
            EntityAttributes.ATTACK_DAMAGE,
            player.getAttributeBaseValue(EntityAttributes.ATTACK_DAMAGE),
            EquipmentSlot.MAINHAND
        );
    }

    private String weaponKind(ItemStack stack) {
        if (stack.isIn(ItemTags.SWORDS)) {
            return "sword";
        }
        if (stack.isOf(Items.TRIDENT)) {
            return "trident";
        }
        if (stack.isIn(ItemTags.AXES)) {
            return "axe";
        }
        if (stack.isOf(Items.BOW)) {
            return "bow";
        }
        if (
            stack.isIn(ItemTags.HOES)
                || stack.isIn(ItemTags.PICKAXES)
                || stack.isIn(ItemTags.SHOVELS)
        ) {
            return "tool";
        }
        return "other";
    }

    /**
     * 画面中央クロスヘア（＋）が刺さっているブロック / エンティティ。
     * MISS・空気はフィールド省略（docs/look-target-observation-plan.md）。
     */
    public JsonObject buildLookTarget(ClientPlayerEntity player, ClientWorld world) {
        MinecraftClient client = MinecraftClient.getInstance();
        if (client == null) {
            return null;
        }
        HitResult hit = client.crosshairTarget;
        if (hit == null || hit.getType() == HitResult.Type.MISS) {
            return null;
        }
        if (hit.getType() == HitResult.Type.BLOCK && hit instanceof BlockHitResult blockHit) {
            BlockPos pos = blockHit.getBlockPos();
            BlockState state = world.getBlockState(pos);
            if (state.isAir()) {
                return null;
            }
            Identifier blockId = Registries.BLOCK.getId(state.getBlock());
            JsonObject json = new JsonObject();
            json.addProperty("kind", "block");
            // nearby_resources と同様 path ベース（サーバ catalog が path キー）
            json.addProperty("name", blockId.getPath());
            double distance = Math.sqrt(
                player.squaredDistanceTo(pos.getX() + 0.5, pos.getY() + 0.5, pos.getZ() + 0.5)
            );
            json.addProperty("distance", round(distance));
            return json;
        }
        if (hit.getType() == HitResult.Type.ENTITY && hit instanceof EntityHitResult entityHit) {
            Entity entity = entityHit.getEntity();
            if (entity == null || !entity.isAlive()) {
                return null;
            }
            Identifier entityId = Registries.ENTITY_TYPE.getId(entity.getType());
            JsonObject json = new JsonObject();
            json.addProperty("kind", "entity");
            json.addProperty("name", entityId.getPath());
            json.add("identity", mobIdentity(entity).toJson());
            if (entity instanceof LivingEntity) {
                json.add("environment", MobEnvironmentObservation.capture(entity).toJson());
            }
            json.addProperty("distance", round(Math.sqrt(player.squaredDistanceTo(entity))));
            return json;
        }
        return null;
    }

    public Double estimateRespawnDistance(ClientWorld world, BlockPos origin) {
        if (!this.respawnPointObserved || this.observedRespawnPos == null || this.observedRespawnDimension == null) {
            return null;
        }
        String currentDimension = world.getRegistryKey().getValue().toString();
        if (!this.observedRespawnDimension.equals(currentDimension)) {
            return null;
        }
        return Math.sqrt(origin.getSquaredDistance(this.observedRespawnPos));
    }

    public String resolvePlayerName(ClientPlayerEntity player) {
        if (!this.playerNameOverride.isBlank()) {
            return this.playerNameOverride;
        }
        return player.getName().getString();
    }

    public boolean isPlayerDead(ClientPlayerEntity player) {
        return !player.isAlive() || player.getHealth() <= 0.0f;
    }

    public String resolveDeathCause(ClientPlayerEntity player) {
        try {
            return player.getDamageTracker().getDeathMessage().getString();
        } catch (Exception ignored) {
            return "unknown";
        }
    }
}
