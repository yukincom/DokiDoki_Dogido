package dogido.lab;

import dogido.lab.api.BodyProtocol.*;
import dogido.lab.control.BodyVitals;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.LinkedHashMap;
import java.util.UUID;
import net.minecraft.block.Block;
import net.minecraft.block.BlockState;
import net.minecraft.block.Blocks;
import net.minecraft.block.CropBlock;
import net.minecraft.block.SweetBerryBushBlock;
import net.minecraft.block.ShapeContext;
import net.minecraft.component.DataComponentTypes;
import net.minecraft.component.type.FoodComponent;
import net.minecraft.entity.ItemEntity;
import net.minecraft.item.Item;
import net.minecraft.item.ItemStack;
import net.minecraft.item.Items;
import net.minecraft.registry.Registries;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.state.property.Properties;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Box;
import net.minecraft.util.math.Vec3d;

/**
 * 身体から見通せる食料だけを観測し、指定された一件を到達・現物再照合後に消費する。
 * 入力はserver側の身体とfood ID、出力は不変の候補/実行理由。探索先や食べる時機は選ばない。
 * DROP itemと成熟した可食植物だけを扱い、チェスト/プレイヤーinventoryから自動取得しない。
 */
final class BodyFood {
    static final double OBSERVE_RADIUS = 8, REACH = 2;
    static final int MAX_TARGETS = 16, FRESH_TICKS = 100;
    private final DogidoEntity body;
    private final Map<String,FoodTarget> observed = new LinkedHashMap<>();
    private long observedTick = -1;
    private record Plant(BlockPos position, BlockState state, Item food, BlockState after) {}
    BodyFood(DogidoEntity body) { this.body = body; }
    void clear() { observed.clear(); observedTick = -1; }
    private ServerWorld world() { return (ServerWorld)body.getEntityWorld(); }
    private long tick() { return world().getServer().getTicks(); }
    private Vec3d point(Position p) { return new Vec3d(p.x(),p.y(),p.z()); }
    private boolean visible(Vec3d target, BlockPos foodBlock) {
        Vec3d eye = body.getEyePos();
        BodySpace space = new BodySpace(world(),body);
        return BodyVisibility.trace(world(),space::loaded,eye,target,ShapeContext.of(body),BodyVisibility.Purpose.FOOD,foodBlock)
            == BodyVisibility.Trace.CLEAR;
    }
    private static int nutrition(ItemStack stack) {
        FoodComponent food = stack.get(DataComponentTypes.FOOD);
        return food == null ? 0 : food.nutrition();
    }
    static boolean edible(ItemStack stack) { return !stack.isEmpty() && nutrition(stack) > 0; }
    private static FoodTarget target(String id, FoodKind kind, Vec3d p, ItemStack stack) {
        return new FoodTarget(id,kind,new Position(p.x,p.y,p.z),Registries.ITEM.getId(stack.getItem()).toString(),nutrition(stack));
    }
    FoodObservation observe() {
        List<FoodTarget> found = new ArrayList<>();
        Vec3d center = body.getEntityPos();
        for (ItemEntity item : world().getEntitiesByClass(ItemEntity.class,body.getBoundingBox().expand(OBSERVE_RADIUS),e -> !e.isRemoved() && edible(e.getStack()))) {
            if (center.distanceTo(item.getEntityPos()) <= OBSERVE_RADIUS && visible(item.getEntityPos().add(0,.15,0),null))
                found.add(target("item:"+item.getUuid(),FoodKind.DROPPED_ITEM,item.getEntityPos(),item.getStack()));
        }
        for (BlockPos p : BlockPos.iterate(body.getBlockPos().add(-8,-8,-8),body.getBlockPos().add(8,8,8))) {
            Vec3d location = p.toCenterPos();
            if (center.distanceTo(location) > OBSERVE_RADIUS || !new BodySpace(world(),body).loaded(new Box(p))) continue;
            Plant plant = plant(p);
            if (plant != null && visible(location,p)) found.add(target("plant:"+p.getX()+","+p.getY()+","+p.getZ(),FoodKind.PLANT,location,plant.food().getDefaultStack()));
        }
        found.sort(Comparator.comparingDouble(f -> point(f.position()).squaredDistanceTo(center)));
        boolean truncated = found.size() > MAX_TARGETS;
        List<FoodTarget> result = List.copyOf(found.subList(0,Math.min(found.size(),MAX_TARGETS)));
        observed.clear(); result.forEach(f -> observed.put(f.id(),f)); observedTick = tick();
        return new FoodObservation(ObservationStatus.OBSERVED,observedTick,result,truncated);
    }
    private Plant plant(BlockPos p) {
        BlockState state = world().getBlockState(p);
        if (state.isOf(Blocks.SWEET_BERRY_BUSH) && state.get(SweetBerryBushBlock.AGE) >= 2)
            return new Plant(p.toImmutable(),state,Items.SWEET_BERRIES,state.with(SweetBerryBushBlock.AGE,1));
        if ((state.isOf(Blocks.CAVE_VINES) || state.isOf(Blocks.CAVE_VINES_PLANT)) && state.get(Properties.BERRIES))
            return new Plant(p.toImmutable(),state,Items.GLOW_BERRIES,state.with(Properties.BERRIES,false));
        if (state.getBlock() instanceof CropBlock crop && crop.isMature(state)) {
            Item item = state.isOf(Blocks.CARROTS) ? Items.CARROT : state.isOf(Blocks.POTATOES) ? Items.POTATO
                : state.isOf(Blocks.BEETROOTS) ? Items.BEETROOT : null;
            if (item != null) return new Plant(p.toImmutable(),state,item,crop.withAge(0));
        }
        return null;
    }
    String admission(String id) {
        if (body.downed()) return "body_downed";
        if (body.getHealth() >= body.getMaxHealth()) return "health_full";
        FoodTarget expected = observed.get(id);
        if (expected == null) return "food_not_observed";
        if (observedTick < 0 || tick()-observedTick > FRESH_TICKS) return "food_observation_expired";
        Vec3d location;
        ItemStack stack;
        BlockPos block = null;
        if (expected.kind() == FoodKind.DROPPED_ITEM) {
            var entity = world().getEntity(UUID.fromString(id.substring(5)));
            if (!(entity instanceof ItemEntity item) || item.isRemoved()) return "food_changed";
            stack = item.getStack(); location = item.getEntityPos();
        } else {
            block = BlockPos.ofFloored(point(expected.position()));
            if (!new BodySpace(world(),body).loaded(new Box(block))) return "food_chunk_not_loaded";
            Plant plant = plant(block);
            if (plant == null) return "food_changed";
            stack = plant.food().getDefaultStack(); location = block.toCenterPos();
        }
        if (!edible(stack) || !Registries.ITEM.getId(stack.getItem()).toString().equals(expected.itemId())) return "food_changed";
        if (body.getEntityPos().distanceTo(location) > REACH) return "food_out_of_reach";
        if (!visible(location.add(0,block == null ? .15 : 0,0),block)) return "food_occluded";
        return null;
    }
    /** 受付後にも同じスレッドで現物を照合する。成功した一個だけに消費効果と回復を適用する。 */
    String consume(String id) {
        String problem = admission(id);
        if (problem != null) return problem;
        FoodTarget expected = observed.get(id);
        ItemStack serving;
        if (expected.kind() == FoodKind.DROPPED_ITEM) {
            ItemEntity item = (ItemEntity)world().getEntity(UUID.fromString(id.substring(5)));
            serving = item.getStack().split(1);
            if (item.getStack().isEmpty()) item.discard();
        } else {
            Plant plant = plant(BlockPos.ofFloored(point(expected.position())));
            if (!world().setBlockState(plant.position(),plant.after(),Block.NOTIFY_ALL)) return "food_changed";
            serving = plant.food().getDefaultStack();
        }
        consumeServing(serving);
        observed.remove(id);
        return "food_consumed";
    }
    void consumeServing(ItemStack serving) {
        int nutrition = nutrition(serving);
        ItemStack remainder = serving.finishUsing(world(),body);
        // 通常の消費効果（例: 腐った肉）も身体へ適用し、HP0を食事で取り消さない。
        float heal = BodyVitals.foodRecovery(nutrition,body.getHealth(),body.getMaxHealth(),body.downed());
        if (heal > 0) body.heal(heal);
        if (!remainder.isEmpty()) world().spawnEntity(new ItemEntity(world(),body.getX(),body.getY(),body.getZ(),remainder));
    }
}
