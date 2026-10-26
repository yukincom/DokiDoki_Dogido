package dogido.fabric.observation.world;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import dogido.fabric.observation.NearbyComposition;
import dogido.fabric.observation.ObservationOrigin;
import dogido.fabric.observation.player.PlayerObserver;
import dogido.fabric.observation.sound.WorldSoundFacts;
import dogido.fabric.observation.world.PortalObservationScanner.PortalObservation;
import dogido.fabric.observation.world.PortalObservationTracker;
import dogido.fabric.observation.world.ShelterObservation.LocalSpaceObservation;
import java.util.ArrayDeque;
import java.util.Comparator;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Optional;
import java.util.Set;
import net.minecraft.block.BlockState;
import net.minecraft.client.MinecraftClient;
import net.minecraft.client.network.ClientPlayerEntity;
import net.minecraft.client.world.ClientWorld;
import net.minecraft.entity.Entity;
import net.minecraft.entity.ItemEntity;
import net.minecraft.entity.passive.SheepEntity;
import net.minecraft.item.BlockItem;
import net.minecraft.item.ItemStack;
import net.minecraft.registry.Registries;
import net.minecraft.registry.RegistryKey;
import net.minecraft.registry.RegistryKeys;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.world.ServerWorld;
import net.minecraft.structure.StructureStart;
import net.minecraft.util.Identifier;
import net.minecraft.util.hit.BlockHitResult;
import net.minecraft.util.hit.HitResult;
import net.minecraft.util.math.BlockPos;
import net.minecraft.util.math.Vec3d;
import net.minecraft.world.Heightmap;
import net.minecraft.world.RaycastContext;
import net.minecraft.world.World;

import static dogido.fabric.observation.MobIdentityObservation.entityTypeName;
import static dogido.fabric.observation.ObservationNumbers.round;
import static dogido.fabric.observation.ObservationTime.ticksSince;
import static dogido.fabric.observation.player.ItemIdentity.itemId;
import static dogido.fabric.observation.sound.WorldSoundFacts.ENDER_EYE_LAUNCH_TTL_TICKS;
import static dogido.fabric.observation.sound.WorldSoundFacts.OMINOUS_SOUND_TTL_TICKS;
import static dogido.fabric.observation.sound.WorldSoundFacts.WEATHER_SOUND_TTL_TICKS;
import static dogido.fabric.observation.world.BlockMaterials.classifyBrokenBlockMaterial;
import static dogido.fabric.observation.world.BlockMaterials.isAirExposedNearbyResource;
import static dogido.fabric.observation.world.BlockMaterials.mergeNearbyResource;
import static dogido.fabric.observation.world.BlockMaterials.nearbyResourceNameForBlock;
import static dogido.fabric.observation.world.BossSceneObservation.detectBossOmenKind;
import static dogido.fabric.observation.world.LightObservation.observeNearbyDamagingLightSources;
import static dogido.fabric.observation.world.LightObservation.observeNearbyLightSources;
import static dogido.fabric.observation.world.LightObservation.estimateConnectedDarkVolume;
import static dogido.fabric.observation.world.LightObservation.estimateDangerDarkness;
import static dogido.fabric.observation.world.LightObservation.estimateNearestDarkSpawnDistance;
import static dogido.fabric.observation.world.LightObservation.isStandingOnMagmaBlock;
import static dogido.fabric.observation.world.PortalObservationScanner.scanNearbyEndPortalFrame;
import static dogido.fabric.observation.world.PortalObservationScanner.scanNearbyPortals;
import static dogido.fabric.observation.world.ShelterObservation.countCardinalWalls;
import static dogido.fabric.observation.world.ShelterObservation.countDoubleHeightOpenSides;
import static dogido.fabric.observation.world.ShelterObservation.countDraftyOpenings;
import static dogido.fabric.observation.world.ShelterObservation.countNearbyBeds;
import static dogido.fabric.observation.world.ShelterObservation.observeNearbyDoors;
import static dogido.fabric.observation.world.ShelterObservation.countNearbySleepingPeople;
import static dogido.fabric.observation.world.ShelterObservation.estimateEnclosureScore;
import static dogido.fabric.observation.world.ShelterObservation.hasNearbyWindow;
import static dogido.fabric.observation.world.ShelterObservation.isSafeZoneWithDoor;
import static dogido.fabric.observation.world.ShelterObservation.observeLocalSpace;
import static dogido.fabric.observation.world.WaterObservation.estimateSubmergedDepthBlocks;
import static dogido.fabric.observation.world.WaterObservation.isWaterContext;

public final class WorldObserver {
    private static final int LIGHTNING_STRIKE_TTL_TICKS = 40;
    private static final int RECENT_BLOCK_BREAK_TTL_TICKS = 200;
    private static final double DROPPED_ITEM_SCAN_RADIUS = 8.0;
    private static final int MAX_DROPPED_ITEM_KINDS = 16;
    private final Deque<BlockBreakObservation> recentBlockBreakObservations = new ArrayDeque<>();
    private long lastNearbyLightningObservedTick = -1000;
    private double lastNearbyLightningDistance = 99.0;
    // プレイヤー座標を含む構造物 id（統合サーバーのスレッドで照会した結果のキャッシュ）
    private volatile String structureAtPlayer = null;
    private volatile StructureAtOrigin structureAtBody;
    private record StructureAtOrigin(BlockPos position, RegistryKey<World> dimension, String name) {}
    private long lastStructureProbeTick = -1000;
    private volatile long structureGeneration;
    private final PortalObservationTracker portalObservationTracker = new PortalObservationTracker();
    private final NearbyComposition.RangeTracker nearbyBlockKinds =
        new NearbyComposition.RangeTracker(0.0, NearbyComposition.BLOCK_MARGIN);

    private final int snapshotIntervalTicks;
    private final PlayerObserver playerObserver;
    private final java.util.function.Supplier<WorldSoundFacts> soundFacts;

    public WorldObserver(int snapshotIntervalTicks, PlayerObserver playerObserver,
            java.util.function.Supplier<WorldSoundFacts> soundFacts) {
        this.snapshotIntervalTicks = snapshotIntervalTicks;
        this.playerObserver = playerObserver;
        this.soundFacts = soundFacts;
    }

    public JsonObject buildWorld(ClientPlayerEntity player, ClientWorld world, long tickCounter) {
        return buildWorld(ObservationOrigin.player(player),world,tickCounter);
    }
    /** 環境の受け手だけを切り替える共通収集。酸素・リスポーン等のplayer所有情報は身体へ投影しない。 */
    public JsonObject buildWorld(ObservationOrigin receiver, ClientWorld world, long tickCounter) {
        BlockPos pos = receiver.blockPosition();
        LocalSpaceObservation space = observeLocalSpace(world, pos);
        JsonObject json = new JsonObject();
        long timeOfDay = world.getTimeOfDay() % 24000L;
        boolean submerged = receiver.touchingWater(world) || receiver.submerged(world);

        if (usesDayNightCycle(world)) {
            json.addProperty("time_of_day", (int) timeOfDay);
            json.addProperty("time_phase", classifyTimePhase(timeOfDay));
        }
        json.addProperty("weather", classifyWeather(world));
        json.addProperty("biome", resolveBiomeName(world, pos));
        String structureName = this.structureAtPlayer;
        if (receiver.body()) {
            StructureAtOrigin observed = this.structureAtBody;
            structureName = observed != null && observed.position().equals(pos)
                && observed.dimension().equals(world.getRegistryKey()) ? observed.name() : null;
        }
        if (structureName != null && !structureName.isBlank()) {
            json.addProperty("structure", structureName);
        }
        json.addProperty("local_light", space.localLight());
        json.addProperty("sky_visible", space.skyVisible());
        json.addProperty("game_paused", MinecraftClient.getInstance().isPaused());
        if (usesDayNightCycle(world)) {
            int surfaceY = world.getTopY(Heightmap.Type.MOTION_BLOCKING_NO_LEAVES, pos.getX(), pos.getZ());
            json.addProperty("surface_y", surfaceY);
            json.addProperty("depth_below_surface", Math.max(0, surfaceY - pos.getY()));
        }
        double ceilingHeight = space.overhead().clearance();
        String overheadCoverType = space.overhead().coverType();
        double enclosureScore = estimateEnclosureScore(world, pos, space.skyVisible());
        var doors = observeNearbyDoors(world, pos);
        int nearbyDoorCount = doors.count();
        int openDoorCount = doors.openCount();
        boolean nearbyWindowPresent = hasNearbyWindow(world, pos);
        int nearbyBedCount = countNearbyBeds(world, pos);
        int nearbySleepingPeopleCount = countNearbySleepingPeople(world, receiver);
        int draftyOpeningCount = countDraftyOpenings(world, pos);
        int submergedDepthBlocks = estimateSubmergedDepthBlocks(world, pos, submerged);
        int nearbyFireflyBushCount = countNearbyFireflyBushes(world, pos);
        int cardinalWallCount = countCardinalWalls(world, pos);
        int doubleHeightOpenSideCount = countDoubleHeightOpenSides(world, pos);
        var lightSources = observeNearbyLightSources(world, pos);
        int nearbyLightSourceCount = lightSources.count();
        double nearestLightSourceDistance = lightSources.nearestDistance();
        var damagingLightSources = observeNearbyDamagingLightSources(world, pos);
        int nearbyDamagingLightSourceCount = damagingLightSources.count();
        double nearestDamagingLightSourceDistance = damagingLightSources.nearestDistance();
        boolean standingOnMagmaBlock = isStandingOnMagmaBlock(world, pos);
        String bossOmenKind = detectBossOmenKind(receiver.player(), world, pos);
        WorldSoundFacts sound = this.soundFacts.get();
        long ominousSoundRecentMs = ticksSince(tickCounter, sound.ominousTick());
        long rainSoundRecentMs = ticksSince(tickCounter, sound.rainTick());
        long thunderSoundRecentMs = ticksSince(tickCounter, sound.thunderTick());
        long nearbyLightningRecentMs = ticksSince(tickCounter, this.lastNearbyLightningObservedTick);
        Double respawnDistance = receiver.body() ? null : this.playerObserver.estimateRespawnDistance(world, pos);
        json.addProperty("ceiling_height", round(ceilingHeight));
        json.addProperty("ceiling_observed", space.overhead().ceilingObserved());
        json.addProperty("overhead_cover_type", overheadCoverType);
        json.addProperty("is_submerged", submerged);
        json.addProperty("submerged_depth_blocks", submergedDepthBlocks);
        if (!receiver.body()) json.addProperty("air_supply", receiver.player().getAir());
        json.addProperty("nearby_door_count", nearbyDoorCount);
        json.addProperty("open_door_count", openDoorCount);
        json.addProperty("nearby_window_present", nearbyWindowPresent);
        json.addProperty("nearby_bed_count", nearbyBedCount);
        json.addProperty("nearby_sleeping_people_count", nearbySleepingPeopleCount);
        json.addProperty("drafty_opening_count", draftyOpeningCount);
        json.addProperty("nearby_firefly_bush_count", nearbyFireflyBushCount);
        if (!receiver.body()) json.addProperty("respawn_point_set", this.playerObserver.respawnPointObserved());
        json.addProperty("cardinal_wall_count", cardinalWallCount);
        json.addProperty("double_height_open_side_count", doubleHeightOpenSideCount);
        json.addProperty("standing_on_magma_block", standingOnMagmaBlock);
        if (bossOmenKind != null) {
            json.addProperty("boss_omen_kind", bossOmenKind);
        }
        if (!sound.ominousKind().isBlank() && ominousSoundRecentMs <= OMINOUS_SOUND_TTL_TICKS * 50L) {
            json.addProperty("ominous_sound_kind", sound.ominousKind());
            json.addProperty("ominous_sound_recent_ms", ominousSoundRecentMs);
        }
        if (rainSoundRecentMs <= WEATHER_SOUND_TTL_TICKS * 50L) {
            json.addProperty("rain_sound_recent_ms", rainSoundRecentMs);
        }
        if (thunderSoundRecentMs <= WEATHER_SOUND_TTL_TICKS * 50L) {
            json.addProperty("thunder_sound_recent_ms", thunderSoundRecentMs);
        }
        if (nearbyLightningRecentMs <= LIGHTNING_STRIKE_TTL_TICKS * 50L && this.lastNearbyLightningDistance <= 30.0) {
            json.addProperty("nearby_lightning_strike_recent_ms", nearbyLightningRecentMs);
            json.addProperty("nearby_lightning_strike_distance", round(this.lastNearbyLightningDistance));
        }
        long enderEyeLaunchRecentMs = ticksSince(tickCounter, sound.enderEyeTick());
        if (enderEyeLaunchRecentMs <= ENDER_EYE_LAUNCH_TTL_TICKS * 50L) {
            json.addProperty("ender_eye_launch_recent_ms", enderEyeLaunchRecentMs);
        }
        if (respawnDistance != null) {
            json.addProperty("respawn_distance", round(respawnDistance));
        }
        json.addProperty(
            "safe_zone_with_door",
            isSafeZoneWithDoor(space, submerged, nearbyDoorCount, enclosureScore)
        );

        int connectedDarkVolume = estimateConnectedDarkVolume(world, pos);
        double nearestDarkSpawnDistance = estimateNearestDarkSpawnDistance(world, pos);
        double darknessScore = estimateDangerDarkness(
            space,
            enclosureScore,
            connectedDarkVolume,
            nearestDarkSpawnDistance,
            nearbyLightSourceCount,
            nearestLightSourceDistance,
            cardinalWallCount
        );

        json.addProperty("enclosure_score", round(enclosureScore));
        json.addProperty("connected_dark_volume", connectedDarkVolume);
        json.addProperty("nearest_dark_spawn_distance", round(nearestDarkSpawnDistance));
        json.addProperty("nearby_light_source_count", nearbyLightSourceCount);
        json.addProperty("nearest_light_source_distance", round(nearestLightSourceDistance));
        json.addProperty("nearby_damaging_light_source_count", nearbyDamagingLightSourceCount);
        json.addProperty("nearest_damaging_light_source_distance", round(nearestDamagingLightSourceDistance));
        json.addProperty("danger_darkness_score", round(darknessScore));

        PortalObservation portalInfo = scanNearbyPortals(receiver, world, pos, this.portalObservationTracker, tickCounter, this.snapshotIntervalTicks);
        if (portalInfo != null) {
            json.addProperty("nearby_portal_type", portalInfo.type());
            json.addProperty("nearby_portal_distance", round(portalInfo.distance()));
            json.addProperty("nearby_portal_encounter", portalInfo.encounter());
        }

        double frameDistance = scanNearbyEndPortalFrame(world, pos);
        if (frameDistance >= 0) {
            json.addProperty("nearby_end_portal_frame_distance", round(frameDistance));
        }

        return json;
    }

    /** 直前の身体用種類走査で実際に見えた各種類の最寄り一点。player観測へは混ぜない。 */
    public record VisibleBlock(BlockPos position,String typeId,String label) {}
    private java.util.List<VisibleBlock> bodyVisibleBlocks = java.util.List.of();
    public java.util.List<VisibleBlock> bodyVisibleBlocks() { return bodyVisibleBlocks; }

    public JsonObject buildNearbyBlocks(ClientPlayerEntity player, ClientWorld world, boolean captureNearbyKinds) {
        return buildNearbyBlocks(ObservationOrigin.player(player),world,captureNearbyKinds);
    }
    /** 種類・資源の分類は共有し、身体の資源は範囲・FOV・見通しを満たす対象だけにする。 */
    public JsonObject buildNearbyBlocks(ObservationOrigin receiver, ClientWorld world, boolean captureNearbyKinds) {
        BlockPos origin = receiver.blockPosition();
        Map<String, Double> nearestDistances = new LinkedHashMap<>();
        Map<String, String> resourceTypes = new LinkedHashMap<>();
        java.util.Set<String> observedTypes = new java.util.TreeSet<>();

        bodyVisibleBlocks = java.util.List.of();
        java.util.List<VisibleBlock> visibleBlocks = new java.util.ArrayList<>();
        int margin = captureNearbyKinds ? NearbyComposition.BLOCK_MARGIN : 0;
        java.util.List<BlockPos> samples = new java.util.ArrayList<>();
        for (int dx = -8 - margin; dx <= 8 + margin; dx += 1) {
            for (int dy = -3 - margin; dy <= 4 + margin; dy += 1) {
                for (int dz = -8 - margin; dz <= 8 + margin; dz += 1) {
                    samples.add(origin.add(dx,dy,dz));
                }
            }
        }
        // 既存範囲を近い順にたどり、一種類につき最初の実視認点だけを操作候補へ残す。
        if (receiver.body()) samples.sort(java.util.Comparator.comparingDouble(pos -> Vec3d.ofCenter(pos).squaredDistanceTo(receiver.position())));
        for (BlockPos sample : samples) {
            int dx = sample.getX()-origin.getX(), dy = sample.getY()-origin.getY(), dz = sample.getZ()-origin.getZ();
            BlockState state = world.getBlockState(sample);
            if (state.isAir()) continue;
            double padding = NearbyComposition.blockPadding(dx, dy, dz);
            String blockId = Registries.BLOCK.getId(state.getBlock()).toString();
            boolean candidate = captureNearbyKinds && !observedTypes.contains(blockId)
                && this.nearbyBlockKinds.allows(blockId, padding);
            if ((padding > 0 && !candidate) || !isAirExposedNearbyResource(world, sample)) continue;
            String resourceName = nearbyResourceNameForBlock(state);
            boolean visible = !receiver.body();
            if (receiver.body() && (candidate || padding == 0 && resourceName != null)) {
                visible = receiver.visible(world,Vec3d.ofCenter(sample),sample,21.0);
            } else if (candidate) {
                // player経路のOUTLINE/流体条件は従来通り。身体は共有Body APIの視認判定を使う。
                BlockHitResult hit = world.raycast(new RaycastContext(receiver.eyePosition(),
                    Vec3d.ofCenter(sample), RaycastContext.ShapeType.OUTLINE,
                    RaycastContext.FluidHandling.ANY, receiver.player()));
                visible = hit.getType() == HitResult.Type.MISS || hit.getBlockPos().equals(sample);
            }
            if (candidate && visible) {
                observedTypes.add(blockId);
                if (receiver.body()) visibleBlocks.add(new VisibleBlock(sample.toImmutable(),blockId,state.getBlock().getName().getString()));
            }
            if (receiver.body() && !visible) continue;
            if (padding == 0 && resourceName != null) {
                double distance = Math.sqrt(sample.getSquaredDistance(origin));
                mergeNearbyResource(resourceTypes, nearestDistances, "block", resourceName, distance);
            }
        }
        bodyVisibleBlocks = java.util.List.copyOf(visibleBlocks);

        for (Entity entity : world.getOtherEntities(receiver.player(), new net.minecraft.util.math.Box(
            origin.getX() - 8.0, origin.getY() - 3.0, origin.getZ() - 8.0,
            origin.getX() + 9.0, origin.getY() + 5.0, origin.getZ() + 9.0
        ))) {
            if (!(entity instanceof SheepEntity sheep) || !sheep.isAlive()) {
                continue;
            }
            if (receiver.body() && !receiver.visible(world,sheep,16.0)) continue;
            String resourceName = sheep.getColor().asString() + "_wool";
            double distance = receiver.distanceTo(sheep);
            mergeNearbyResource(resourceTypes, nearestDistances, "mob", resourceName, distance);
        }

        JsonArray array = new JsonArray();
        nearestDistances.entrySet().stream()
            .sorted(Map.Entry.comparingByValue())
            .limit(16)
            .forEach(entry -> {
                JsonObject item = new JsonObject();
                item.addProperty("type", resourceTypes.get(entry.getKey()));
                item.addProperty("name", entry.getKey());
                item.addProperty("distance", round(entry.getValue()));
                array.add(item);
            });
        JsonObject result = new JsonObject();
        result.add("resources", array);
        if (captureNearbyKinds) result.add("types", this.nearbyBlockKinds.finish(observedTypes));
        return result;
    }

    public JsonArray buildDroppedItems(ClientPlayerEntity player, ClientWorld world) {
        return buildDroppedItems(ObservationOrigin.player(player),world);
    }
    /** 同じ品目集約を使い、身体経路では視認できた落下物だけを材料にする。 */
    public JsonArray buildDroppedItems(ObservationOrigin receiver, ClientWorld world) {
        BlockPos origin = receiver.blockPosition();
        double radius = DROPPED_ITEM_SCAN_RADIUS;
        Map<String, DroppedItemObservation> byItemId = new LinkedHashMap<>();
        for (Entity entity : world.getOtherEntities(receiver.player(), new net.minecraft.util.math.Box(
            origin.getX() - radius,
            origin.getY() - radius,
            origin.getZ() - radius,
            origin.getX() + radius + 1.0,
            origin.getY() + radius + 1.0,
            origin.getZ() + radius + 1.0
        ))) {
            if (!(entity instanceof ItemEntity itemEntity) || !itemEntity.isAlive()) {
                continue;
            }
            if (receiver.body() && !receiver.visible(world,itemEntity,DROPPED_ITEM_SCAN_RADIUS)) continue;
            ItemStack stack = itemEntity.getStack();
            if (stack.isEmpty()) {
                continue;
            }
            String itemId = Registries.ITEM.getId(stack.getItem()).getPath();
            double distance = receiver.distanceTo(itemEntity);
            int ageTicks = Math.max(0, itemEntity.getItemAge());
            boolean blockItem = stack.getItem() instanceof BlockItem;
            boolean miningRelated = isMiningRelatedDroppedItem(stack, itemId);
            DroppedItemObservation previous = byItemId.get(itemId);
            if (previous == null) {
                byItemId.put(
                    itemId,
                    new DroppedItemObservation(
                        itemId,
                        stack.getCount(),
                        1,
                        distance,
                        ageTicks,
                        blockItem,
                        miningRelated
                    )
                );
                continue;
            }
            byItemId.put(
                itemId,
                new DroppedItemObservation(
                    itemId,
                    previous.count() + stack.getCount(),
                    previous.entityCount() + 1,
                    Math.min(previous.distance(), distance),
                    Math.min(previous.ageTicks(), ageTicks),
                    previous.blockItem() || blockItem,
                    previous.miningRelated() || miningRelated
                )
            );
        }

        JsonArray array = new JsonArray();
        byItemId.values().stream()
            .sorted(Comparator.comparingDouble(DroppedItemObservation::distance))
            .limit(MAX_DROPPED_ITEM_KINDS)
            .forEach(observation -> {
                JsonObject item = new JsonObject();
                item.addProperty("name", observation.itemId());
                item.addProperty("count", observation.count());
                item.addProperty("entity_count", observation.entityCount());
                item.addProperty("distance", round(observation.distance()));
                item.addProperty("age_ms", observation.ageTicks() * 50L);
                item.addProperty("block_item", observation.blockItem());
                item.addProperty("mining_related", observation.miningRelated());
                array.add(item);
            });
        return array;
    }

    private boolean isMiningRelatedDroppedItem(ItemStack stack, String itemId) {
        if (stack.getItem() instanceof BlockItem blockItem) {
            return !"other".equals(classifyBrokenBlockMaterial(blockItem.getBlock().getDefaultState()));
        }
        return Set.of(
            "coal",
            "raw_copper",
            "raw_gold",
            "raw_iron",
            "diamond",
            "emerald",
            "lapis_lazuli",
            "redstone",
            "quartz",
            "flint"
        ).contains(itemId);
    }

    public void rememberBlockBreak(BlockState state, long tickCounter) {
        if (state == null || state.isAir()) {
            return;
        }
        String blockId = Registries.BLOCK.getId(state.getBlock()).getPath();
        recentBlockBreakObservations.addLast(
            new BlockBreakObservation(
                blockId,
                classifyBrokenBlockMaterial(state),
                tickCounter
            )
        );
        expireBlockBreakObservations(tickCounter);
    }

    public void expireBlockBreakObservations(long tickCounter) {
        while (!recentBlockBreakObservations.isEmpty()) {
            BlockBreakObservation oldest = recentBlockBreakObservations.peekFirst();
            if (tickCounter - oldest.observedTick() <= RECENT_BLOCK_BREAK_TTL_TICKS) {
                break;
            }
            recentBlockBreakObservations.removeFirst();
        }
    }

    public JsonArray buildRecentBlockBreaks(long tickCounter) {
        JsonArray array = new JsonArray();
        var iterator = recentBlockBreakObservations.descendingIterator();
        while (iterator.hasNext() && array.size() < 12) {
            BlockBreakObservation observation = iterator.next();
            JsonObject item = new JsonObject();
            item.addProperty("name", observation.blockId());
            item.addProperty("material", observation.material());
            item.addProperty("age_ms", ticksSince(tickCounter, observation.observedTick()));
            array.add(item);
        }
        return array;
    }

    public void probeStructureAtPlayer(MinecraftClient client, ClientPlayerEntity player, ClientWorld world, long tickCounter) {
        probeStructureAtOrigin(client,ObservationOrigin.player(player),world,tickCounter);
    }
    /** 受け手の地点をserverへ問い合わせる。独立collectorの世代を照合し、別世界の応答を採用しない。 */
    public void probeStructureAtOrigin(MinecraftClient client, ObservationOrigin receiver, ClientWorld world, long tickCounter) {
        if (tickCounter - this.lastStructureProbeTick < this.snapshotIntervalTicks) {
            return;
        }
        this.lastStructureProbeTick = tickCounter;
        MinecraftServer server = client.getServer();
        if (server == null) {
            // リモートサーバー接続時はクライアントから構造物情報を取得できない
            this.structureAtPlayer = null;
            this.structureAtBody = null;
            return;
        }
        BlockPos pos = receiver.blockPosition();
        long generation = ++this.structureGeneration;
        RegistryKey<World> worldKey = world.getRegistryKey();
        // 構造物参照はサーバー側データなので、統合サーバーのスレッドで照会して結果だけ受け取る
        server.execute(() -> {
            ServerWorld serverWorld = server.getWorld(worldKey);
            if (generation != this.structureGeneration) return;
            if (serverWorld == null) {
                this.structureAtPlayer = null;
                this.structureAtBody = null;
                return;
            }
            // ピース単位の内包判定（バニラの実績判定と同じ基準）
            StructureStart start = serverWorld.getStructureAccessor().getStructureContaining(pos, entry -> true);
            if (start == null || !start.hasChildren()) {
                this.structureAtPlayer = null;
                this.structureAtBody = null;
                return;
            }
            Identifier structureId = serverWorld.getRegistryManager()
                .getOrThrow(RegistryKeys.STRUCTURE)
                .getId(start.getStructure());
            if (generation != this.structureGeneration) return;
            String name = structureId == null ? null : structureId.getPath();
            if (receiver.body()) this.structureAtBody = new StructureAtOrigin(pos,worldKey,name);
            else this.structureAtPlayer = name;
        });
    }

    public void observeNearbyLightning(ClientPlayerEntity player, ClientWorld world, long tickCounter) {
        observeNearbyLightning(ObservationOrigin.player(player),world,tickCounter);
    }
    public void observeNearbyLightning(ObservationOrigin receiver, ClientWorld world, long tickCounter) {
        double nearestDistance = Double.POSITIVE_INFINITY;
        for (Entity entity : world.getOtherEntities(
            null,
            receiver.bounds().expand(30.0, 16.0, 30.0)
        )) {
            if (!"lightning_bolt".equals(entityTypeName(entity))) {
                continue;
            }
            if (receiver.body() && !receiver.visible(world,entity,30.0)) continue;
            double distance = receiver.distanceTo(entity);
            if (distance < nearestDistance) {
                nearestDistance = distance;
            }
        }
        if (nearestDistance == Double.POSITIVE_INFINITY) {
            return;
        }
        this.lastNearbyLightningObservedTick = tickCounter;
        this.lastNearbyLightningDistance = nearestDistance;
    }

    private String classifyTimePhase(long timeOfDay) {
        if (timeOfDay < 2000L) {
            return "morning";
        }
        if (timeOfDay < 11000L) {
            return "day";
        }
        if (timeOfDay < 14000L) {
            return "evening";
        }
        return "night";
    }

    private String classifyWeather(ClientWorld world) {
        if (world.isThundering()) {
            return "thunder";
        }
        if (world.isRaining()) {
            return "rain";
        }
        return "clear";
    }

    private String resolveBiomeName(ClientWorld world, BlockPos pos) {
        Optional<Identifier> biomeId = world.getBiome(pos).getKey().map(key -> key.getValue());
        return biomeId.map(Identifier::getPath).orElse("unknown");
    }

    private boolean usesDayNightCycle(ClientWorld world) {
        return "minecraft:overworld".equals(world.getRegistryKey().getValue().toString());
    }

    private int countNearbyFireflyBushes(ClientWorld world, BlockPos origin) {
        int count = 0;
        for (int dx = -16; dx <= 16; dx += 1) {
            for (int dy = -4; dy <= 6; dy += 1) {
                for (int dz = -16; dz <= 16; dz += 1) {
                    BlockPos sample = origin.add(dx, dy, dz);
                    String blockId = Registries.BLOCK.getId(world.getBlockState(sample).getBlock()).getPath();
                    if ("firefly_bush".equals(blockId)) {
                        count += 1;
                    }
                }
            }
        }
        return count;
    }

    private record BlockBreakObservation(
        String blockId,
        String material,
        long observedTick
    ) {
    }

    private record DroppedItemObservation(
        String itemId,
        int count,
        int entityCount,
        double distance,
        int ageTicks,
        boolean blockItem,
        boolean miningRelated
    ) {
    }

    public void resetRangeTracking() { this.nearbyBlockKinds.reset(); }
    public void resetPortalTracking() { this.portalObservationTracker.reset(); }
    public void resetBlockBreaks() { this.recentBlockBreakObservations.clear(); }
    public void resetEnvironmentHistory() {
        this.lastNearbyLightningObservedTick = -1000;
        this.lastNearbyLightningDistance = 99.0;
        this.structureAtPlayer = null;
        this.structureAtBody = null;
        this.structureGeneration++;
        this.lastStructureProbeTick = -1000;
    }

}
