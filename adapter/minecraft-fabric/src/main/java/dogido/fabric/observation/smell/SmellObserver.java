package dogido.fabric.observation.smell;

import dogido.fabric.observation.ObservationOrigin;

import dogido.fabric.observation.smell.SmellPolicy;
import dogido.fabric.observation.smell.ZombieScentPolicy;
import java.util.ArrayList;
import java.util.List;
import java.util.Set;
import net.minecraft.block.BlockState;
import net.minecraft.block.BrewingStandBlock;
import net.minecraft.block.ComposterBlock;
import net.minecraft.block.entity.CampfireBlockEntity;
import net.minecraft.block.enums.DoubleBlockHalf;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.ItemEntity;
import net.minecraft.entity.LivingEntity;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.registry.entry.RegistryEntry;
import net.minecraft.registry.tag.BlockTags;
import net.minecraft.state.property.Properties;
import net.minecraft.util.math.BlockPos;
import net.minecraft.world.biome.Biome;

import static dogido.fabric.observation.player.ItemIdentity.itemId;
import static dogido.fabric.observation.world.CampfireObservation.isLitCampfire;

public final class SmellObserver {
    private static final long RAIN_AFTER_SMELL_TICKS = 3L * 60L * 20L;

    private List<SmellPolicy.LocatedCandidate> currentBlockSmellCandidates = List.of();
    private SmellPolicy.Observation currentSmellObservation = SmellPolicy.resolve(List.of(), 0, false, null);
    private long lastBlockSmellScanTick = -1000;
    private long rainAfterSmellUntilTick = -1;
    private String previousLocalSmellWeather = null;

    // 嗅覚候補は敵対判定・視認・聴取とは独立。個体や位置は送信しない。
    // scanThreatsを流用すると中立中のゾンビピグリンが抜け、視認後に匂いが消えてしまう。
    // この走査は候補収集だけで、戦闘追跡や敵対判定の一覧には何も追加しない。
    private void collectZombieSmellCandidates(
        ObservationOrigin receiver, ClientWorld world, List<SmellPolicy.Candidate> candidates
    ) {
        for (Entity entity : world.getOtherEntities(receiver.player(),
                receiver.bounds().expand(ZombieScentPolicy.MAX_DISTANCE),
                candidate -> candidate instanceof LivingEntity && candidate.isAlive())) {
            String type = Registries.ENTITY_TYPE.getId(entity.getType()).toString();
            double distance = receiver.distanceTo(entity);
            SmellPolicy.Candidate candidate = SmellPolicy.zombieCandidate(type, entity.getUuid().toString(), distance);
            if (candidate == null) continue;
            candidates.add(candidate);
        }
    }

    // 一回の観測を「収集 → 到達・強度の計算 → マスキング」の順に組み立てる。
    // 匂いから方位は作らず、通信には残った種類と強さを渡す。
    public void observe(
        ClientPlayerEntity player,
        ClientWorld world,
        long tickCounter,
        int snapshotIntervalTicks
    ) {
        observe(ObservationOrigin.player(player),player,world,tickCounter,snapshotIntervalTicks);
    }

    /** 嗅ぐ受け手と持ち物の所有者を分離する。ブロック・雨上がりのキャッシュは受け手ごとのインスタンスが所有する。 */
    public void observe(ObservationOrigin receiver, ClientPlayerEntity owner, ClientWorld world,
            long tickCounter, int snapshotIntervalTicks) {
        BlockPos origin = receiver.blockPosition();
        RegistryEntry<Biome> biomeEntry = world.getBiome(origin);
        Biome biome = biomeEntry.value();
        String localWeather = localSmellWeather(world, origin, biome);
        updateRainAfterSmellWindow(localWeather, tickCounter);
        boolean rainAfterActive = this.rainAfterSmellUntilTick >= tickCounter;

        int temperatureModifier = SmellPolicy.temperatureModifier(
            biome.getTemperature(),
            biome.isCold(origin, world.getSeaLevel())
        );

        if (blockScanDue(tickCounter, snapshotIntervalTicks)) {
            cacheBlockCandidates(scanBlockSmellCandidates(
                world,
                origin,
                SmellPolicy.blockScanDistance(temperatureModifier, rainAfterActive)
            ), tickCounter);
        }

        List<SmellPolicy.Candidate> candidates = new ArrayList<>();
        // 種類の再走査は間引いても、キャッシュした各ブロックまでの現在距離は更新する。
        for (SmellPolicy.LocatedCandidate located : this.currentBlockSmellCandidates) {
            candidates.add(located.observedFrom(receiver.body() ? receiver.position().x : origin.getX(),
                receiver.body() ? receiver.position().y : origin.getY(), receiver.body() ? receiver.position().z : origin.getZ()));
        }
        collectZombieSmellCandidates(receiver, world, candidates);
        collectHotbarSmellCandidates(owner, receiver, candidates);
        collectDroppedItemSmellCandidates(receiver, world, candidates);
        String biomeId = biomeEntry.getKey()
            .map(key -> key.getValue().toString())
            .orElse("unknown");
        SmellPolicy.Candidate biomeCandidate = SmellPolicy.biomeCandidate(biomeId);
        if (biomeCandidate != null) {
            candidates.add(biomeCandidate);
        }

        this.currentSmellObservation = SmellPolicy.resolve(
            candidates,
            temperatureModifier,
            rainAfterActive,
            smellSuppressionReason(receiver, world, localWeather)
        );
    }

    void updateRainAfterSmellWindow(String localWeather, long tickCounter) {
        if (
            "clear".equals(localWeather)
                && ("rain".equals(this.previousLocalSmellWeather)
                    || "thunder_rain".equals(this.previousLocalSmellWeather))
        ) {
            this.rainAfterSmellUntilTick = tickCounter + RAIN_AFTER_SMELL_TICKS;
        } else if (!"clear".equals(localWeather)) {
            this.rainAfterSmellUntilTick = -1;
        }
        this.previousLocalSmellWeather = localWeather;
    }

    private String localSmellWeather(ClientWorld world, BlockPos pos, Biome biome) {
        Biome.Precipitation precipitation = biome.getPrecipitation(pos, world.getSeaLevel());
        if (world.isThundering()) {
            return switch (precipitation) {
                case RAIN -> "thunder_rain";
                case SNOW -> "thunder_snow";
                case NONE -> "thunder_dry";
            };
        }
        if (!world.isRaining()) {
            return "clear";
        }
        return switch (precipitation) {
            case RAIN -> "rain";
            case SNOW -> "snow";
            case NONE -> "dry";
        };
    }

    private String smellSuppressionReason(ObservationOrigin receiver, ClientWorld world, String localWeather) {
        if (receiver.submerged(world)) {
            return "submerged";
        }
        if (localWeather.startsWith("thunder_")) {
            return "thunder";
        }
        if ("rain".equals(localWeather)) {
            return "rain";
        }
        if ("snow".equals(localWeather)) {
            return "snow";
        }
        return null;
    }

    private void collectHotbarSmellCandidates(
        ClientPlayerEntity player,
        ObservationOrigin receiver,
        List<SmellPolicy.Candidate> candidates
    ) {
        for (int slot = 0; slot < net.minecraft.entity.player.PlayerInventory.getHotbarSize(); slot += 1) {
            ItemStack stack = player.getInventory().getStack(slot);
            if (stack.isEmpty()) {
                continue;
            }
            SmellPolicy.Candidate candidate = SmellPolicy.hotbarCandidate(itemId(stack),receiver.distanceTo(player));
            if (candidate != null) {
                candidates.add(candidate);
            }
        }
    }

    private void collectDroppedItemSmellCandidates(
        ObservationOrigin receiver,
        ClientWorld world,
        List<SmellPolicy.Candidate> candidates
    ) {
        double radius = SmellPolicy.MAX_SCAN_DISTANCE;
        for (Entity entity : world.getOtherEntities(receiver.player(), receiver.bounds().expand(radius))) {
            if (!(entity instanceof ItemEntity itemEntity) || !itemEntity.isAlive()) {
                continue;
            }
            ItemStack stack = itemEntity.getStack();
            if (stack.isEmpty()) {
                continue;
            }
            SmellPolicy.Candidate candidate = SmellPolicy.droppedItemCandidate(
                itemId(stack),
                itemEntity.getUuid().toString(),
                receiver.distanceTo(itemEntity)
            );
            if (candidate != null) {
                candidates.add(candidate);
            }
        }
    }

    private List<SmellPolicy.LocatedCandidate> scanBlockSmellCandidates(
        ClientWorld world,
        BlockPos origin,
        int radius
    ) {
        List<SmellPolicy.LocatedCandidate> candidates = new ArrayList<>();
        double radiusSquared = radius * radius;
        for (int dx = -radius; dx <= radius; dx += 1) {
            for (int dy = -radius; dy <= radius; dy += 1) {
                for (int dz = -radius; dz <= radius; dz += 1) {
                    double distanceSquared = (double) dx * dx + (double) dy * dy + (double) dz * dz;
                    if (distanceSquared > radiusSquared) {
                        continue;
                    }
                    BlockPos sample = origin.add(dx, dy, dz);
                    BlockState state = world.getBlockState(sample);
                    if (
                        state.contains(Properties.DOUBLE_BLOCK_HALF)
                            && state.get(Properties.DOUBLE_BLOCK_HALF) == DoubleBlockHalf.UPPER
                    ) {
                        continue;
                    }
                    String blockId = Registries.BLOCK.getId(state.getBlock()).toString();
                    double distance = Math.sqrt(distanceSquared);
                    boolean composterFilled = state.getBlock() instanceof ComposterBlock
                        && state.contains(ComposterBlock.LEVEL)
                        && state.get(ComposterBlock.LEVEL) > 0;
                    boolean brewingOccupied = false;
                    if (state.getBlock() instanceof BrewingStandBlock) {
                        for (var bottleProperty : BrewingStandBlock.BOTTLE_PROPERTIES) {
                            if (state.contains(bottleProperty) && Boolean.TRUE.equals(state.get(bottleProperty))) {
                                brewingOccupied = true;
                                break;
                            }
                        }
                    }
                    SmellPolicy.Candidate blockCandidate = SmellPolicy.blockCandidate(
                        blockId,
                        "block:" + Registries.BLOCK.getId(state.getBlock()).getPath() + "@" + sample.asLong(),
                        distance,
                        composterFilled,
                        brewingOccupied
                    );
                    if (blockCandidate != null) {
                        candidates.add(new SmellPolicy.LocatedCandidate(blockCandidate, sample.getX(), sample.getY(), sample.getZ()));
                    }
                    collectCampfireCookingCandidates(world, sample, state, distance, candidates);
                    if (isRainAfterEarthBlock(state)) {
                        candidates.add(new SmellPolicy.LocatedCandidate(
                            SmellPolicy.rainAfterCandidate("rain_after:earth@" + sample.asLong(), distance),
                            sample.getX(), sample.getY(), sample.getZ()
                        ));
                    }
                }
            }
        }
        return List.copyOf(candidates);
    }

    private void collectCampfireCookingCandidates(
        ClientWorld world,
        BlockPos pos,
        BlockState state,
        double distance,
        List<SmellPolicy.LocatedCandidate> candidates
    ) {
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        if (!isLitCampfire(state, blockId)) {
            return;
        }
        if (!(world.getBlockEntity(pos) instanceof CampfireBlockEntity campfire)) {
            return;
        }
        for (ItemStack cooking : campfire.getItemsBeingCooked()) {
            if (cooking.isEmpty()) {
                continue;
            }
            SmellPolicy.Candidate candidate = SmellPolicy.cookingCandidate(
                itemId(cooking),
                "block:" + blockId + "@" + pos.asLong(),
                distance
            );
            if (candidate != null) {
                candidates.add(new SmellPolicy.LocatedCandidate(candidate,
                    pos.getX(), pos.getY(), pos.getZ()));
            }
        }
    }

    private boolean isRainAfterEarthBlock(BlockState state) {
        if (state.isIn(BlockTags.LEAVES) || state.isIn(BlockTags.DIRT)) {
            return true;
        }
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        return Set.of(
            "grass",
            "short_grass",
            "tall_grass",
            "fern",
            "large_fern",
            "moss_block",
            "moss_carpet",
            "leaf_litter",
            "vine"
        ).contains(blockId);
    }

    public void reset() {
        this.currentBlockSmellCandidates = List.of();
        this.currentSmellObservation = SmellPolicy.resolve(List.of(), 0, false, null);
        this.lastBlockSmellScanTick = -1000;
        this.rainAfterSmellUntilTick = -1;
        this.previousLocalSmellWeather = null;
    }

    boolean blockScanDue(long tickCounter, int snapshotIntervalTicks) {
        return this.lastBlockSmellScanTick < 0
            || tickCounter - this.lastBlockSmellScanTick >= snapshotIntervalTicks;
    }

    void cacheBlockCandidates(List<SmellPolicy.LocatedCandidate> candidates, long tickCounter) {
        this.currentBlockSmellCandidates = candidates;
        this.lastBlockSmellScanTick = tickCounter;
    }

    public Result current() {
        return new Result(this.currentSmellObservation, this.rainAfterSmellUntilTick);
    }

    public record Result(SmellPolicy.Observation observation, long rainAfterWindowId) {}

}
