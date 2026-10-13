package dogido.fabric.observation.smell;

import java.util.List;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

final class SmellPolicyTest {
    @Test void ownerHotbarSmellReachesOnlyTheBodyWithinItsRange() {
        var close = SmellPolicy.resolve(List.of(SmellPolicy.hotbarCandidate("bread",1)),0,false,null);
        var far = SmellPolicy.resolve(List.of(SmellPolicy.hotbarCandidate("bread",30)),0,false,null);
        assertEquals("present",close.status());
        assertEquals("none",far.status());
        assertNull(SmellPolicy.hotbarCandidate("bread",Double.NaN));
        assertEquals(SmellPolicy.hotbarCandidate("bread"),SmellPolicy.hotbarCandidate("bread",0));
    }
    @Test
    void cachedBlockRemeasuresDistanceWithoutProducingDirection() {
        var cached = new SmellPolicy.LocatedCandidate(
            SmellPolicy.blockCandidate("lilac", "block:lilac@position", 5, false, false), 5, 0, 0);
        var before = SmellPolicy.resolve(List.of(cached.observedFrom(0, 0, 0)), 0, false, null);
        var after = SmellPolicy.resolve(List.of(cached.observedFrom(2, 0, 0)), 0, false, null);
        assertEquals(1, before.components().getFirst().effectiveStrength());
        assertEquals(3, after.components().getFirst().effectiveStrength());
        // 同じ距離なら、反対側に立っても同じ匂い観測になる。
        var opposite = SmellPolicy.resolve(List.of(cached.observedFrom(8, 0, 0)), 0, false, null);
        assertEquals(after, opposite);
    }

    @Test
    void mapsBiomeTemperatureToClosedModifierBands() {
        assertEquals(2, SmellPolicy.temperatureModifier(1.25f, false));
        assertEquals(0, SmellPolicy.temperatureModifier(0.8f, false));
        assertEquals(-3, SmellPolicy.temperatureModifier(0.5f, false));
        assertEquals(-5, SmellPolicy.temperatureModifier(0.25f, false));
        assertEquals(-7, SmellPolicy.temperatureModifier(-0.25f, false));
        assertEquals(-5, SmellPolicy.temperatureModifier(0.7f, true));
        assertEquals(8, SmellPolicy.blockScanDistance(2, true));
        assertEquals(5, SmellPolicy.blockScanDistance(0, false));
        assertEquals(3, SmellPolicy.blockScanDistance(-7, false));
    }

    @Test
    void usesActualRegistryIdsAndRejectsCatalogAliases() {
        assertEquals("raw_meat", SmellPolicy.hotbarCandidate("minecraft:beef").smellId());
        assertEquals("cooked_meat", SmellPolicy.hotbarCandidate("minecraft:cooked_beef").smellId());
        assertEquals("raw_fish", SmellPolicy.hotbarCandidate("minecraft:cod").smellId());
        assertNull(SmellPolicy.hotbarCandidate("raw_beef"));
        assertNull(SmellPolicy.hotbarCandidate("steak"));
        assertNull(SmellPolicy.hotbarCandidate("example:bread"));
        assertNull(SmellPolicy.biomeCandidate("example:swamp"));
        assertNull(SmellPolicy.blockCandidate("sulfur_block", "block", 1.0, true, true));
        assertNull(SmellPolicy.blockCandidate("golden_dandelion", "block", 1.0, true, true));
    }

    @Test
    void emptyComposterAndUnoccupiedBrewingStandAreOdorless() {
        assertNull(SmellPolicy.blockCandidate("composter", "a", 1.0, false, false));
        assertNull(SmellPolicy.blockCandidate("brewing_stand", "b", 1.0, false, false));
        assertEquals(
            "composter",
            SmellPolicy.blockCandidate("composter", "a", 1.0, true, false).smellId()
        );
        assertEquals(
            "brewing_stand",
            SmellPolicy.blockCandidate("brewing_stand", "b", 1.0, false, true).smellId()
        );
    }

    @Test
    void onlyConfiguredFlowersSmellAndLilacTravelsFarther() {
        SmellPolicy.Candidate lilac = SmellPolicy.blockCandidate("lilac", "a", 1.0, false, false);
        SmellPolicy.Candidate azalea = SmellPolicy.blockCandidate(
            "flowering_azalea",
            "b",
            1.0,
            false,
            false
        );
        assertEquals(5, lilac.basePropagation());
        assertEquals("pleasant", azalea.valence());
        assertNull(SmellPolicy.blockCandidate("azalea", "c", 1.0, false, false));
        assertNull(SmellPolicy.blockCandidate("closed_eyeblossom", "d", 1.0, false, false));
        assertNull(SmellPolicy.blockCandidate("poppy", "e", 1.0, false, false));
    }

    @Test
    void temperatureChangesReachButKeepsAdjacentMinimum() {
        SmellPolicy.Candidate zombie = SmellPolicy.zombieCandidate("zombie", "z1", 8.0);
        assertEquals(
            "zombie",
            SmellPolicy.resolve(List.of(zombie), 0, false, null).components().getFirst().smellId()
        );
        assertEquals(
            "none",
            SmellPolicy.resolve(List.of(zombie), -7, false, null).status()
        );
        SmellPolicy.Candidate adjacent = SmellPolicy.zombieCandidate("zombie", "z1", 1.0);
        assertEquals(
            1,
            SmellPolicy.resolve(List.of(adjacent), -7, false, null).components().getFirst().effectiveStrength()
        );

        SmellPolicy.Candidate warmDistance = SmellPolicy.zombieCandidate("zombie", "z1", 10.0);
        assertEquals(
            "zombie",
            SmellPolicy.resolve(List.of(warmDistance), 2, false, null).components().getFirst().smellId()
        );
        assertEquals(
            "none",
            SmellPolicy.resolve(List.of(warmDistance), 0, false, null).status()
        );
    }

    @Test
    void hotbarRottenFleshMasksRealZombie() {
        SmellPolicy.Observation winner = SmellPolicy.resolve(
            List.of(
                SmellPolicy.zombieCandidate("zombie", "z1", 4.0),
                SmellPolicy.hotbarCandidate("rotten_flesh")
            ),
            0,
            false,
            null
        );
        assertEquals("rotten_flesh", winner.components().getFirst().smellId());

        SmellPolicy.Observation touchingTie = SmellPolicy.resolve(
            List.of(
                SmellPolicy.zombieCandidate("zombie", "z1", 1.0),
                SmellPolicy.hotbarCandidate("rotten_flesh")
            ),
            0,
            false,
            null
        );
        assertEquals(List.of("rotten_flesh", "zombie"), ids(touchingTie));
    }

    @Test
    void sameSmellDoesNotAccumulateAndDifferentFoodsStaySeparate() {
        SmellPolicy.Candidate breadA = SmellPolicy.hotbarCandidate("bread");
        SmellPolicy.Candidate breadB = SmellPolicy.droppedItemCandidate("bread", "item-2", 1.0);
        SmellPolicy.Observation same = SmellPolicy.resolve(
            List.of(breadA, breadB),
            0,
            false,
            null
        );
        assertEquals("bread", same.components().getFirst().smellId());
        assertEquals(3, same.components().getFirst().effectiveStrength());

        SmellPolicy.Observation foodTie = SmellPolicy.resolve(
            List.of(
                SmellPolicy.hotbarCandidate("bread"),
                SmellPolicy.hotbarCandidate("cookie")
            ),
            0,
            false,
            null
        );
        assertEquals(List.of("bread", "cookie"), ids(foodTie));
    }

    @Test
    void crossCategoryTiePreservesEachIdentityAndStrength() {
        SmellPolicy.Observation observation = SmellPolicy.resolve(
            List.of(
                SmellPolicy.hotbarCandidate("bread"),
                SmellPolicy.biomeCandidate("swamp")
            ),
            0,
            false,
            null
        );
        assertEquals(List.of("bread", "swamp"), ids(observation));
        assertTrue(observation.components().stream().allMatch(c -> c.effectiveStrength() == 3));
    }

    @Test
    void liveCookingIgnoresColdPenaltyButWeatherStillSuppressesEverything() {
        SmellPolicy.Candidate cooking = SmellPolicy.cookingCandidate("beef", "campfire", 3.0);
        SmellPolicy.Observation cold = SmellPolicy.resolve(
            List.of(cooking),
            -7,
            false,
            null
        );
        assertEquals("cooking_meat", cold.components().getFirst().smellId());
        assertEquals(1, cold.components().getFirst().effectiveStrength());

        for (String reason : new String[] {"rain", "snow", "thunder", "submerged"}) {
            SmellPolicy.Observation suppressed = SmellPolicy.resolve(
                List.of(cooking),
                2,
                false,
                reason
            );
            assertEquals("suppressed", suppressed.status());
            assertEquals(reason, suppressed.suppressionReason());
        }
    }

    @Test
    void rainAfterAddsOneToPlantsAndCreatesGenericEarthSmell() {
        SmellPolicy.Candidate lily = SmellPolicy.blockCandidate(
            "lily_of_the_valley",
            "flower",
            1.0,
            false,
            false
        );
        SmellPolicy.Observation dry = SmellPolicy.resolve(List.of(lily), 0, false, null);
        SmellPolicy.Observation wet = SmellPolicy.resolve(List.of(lily), 0, true, null);
        assertEquals(1, dry.components().getFirst().effectiveStrength());
        assertEquals(1, wet.components().getFirst().effectiveStrength());
        assertEquals("lily_of_the_valley", wet.components().getFirst().smellId());
        assertEquals(2, wet.rainAfter().effectiveStrength());
        assertEquals("rain_after", wet.rainAfter().smellId());

        SmellPolicy.Observation inactiveEarth = SmellPolicy.resolve(
            List.of(SmellPolicy.rainAfterCandidate("grass", 1.0)),
            0,
            false,
            null
        );
        assertEquals("none", inactiveEarth.status());

        SmellPolicy.Observation earth = SmellPolicy.resolve(
            List.of(SmellPolicy.rainAfterCandidate("grass", 1.0)),
            0,
            true,
            null
        );
        assertEquals("none", earth.status());
        assertTrue(earth.components().isEmpty());
        assertEquals("rain_after", earth.rainAfter().smellId());
        assertEquals(1, earth.rainAfter().effectiveStrength());
        assertTrue(earth.rainAfterActive());
    }

    @Test
    void rainAfterKeepsNormalMixtureForImmediateReturnAfterOneOutput() {
        var candidates = List.of(SmellPolicy.hotbarCandidate("lily_of_the_valley"),
            SmellPolicy.hotbarCandidate("bread"));
        var wet = SmellPolicy.resolve(candidates, 0, true, null);
        assertEquals(List.of("bread", "lily_of_the_valley"), ids(wet));
        assertEquals("hotbar", wet.rainAfter().sourceKind());
        assertEquals(2, wet.rainAfter().effectiveStrength());
        var dry = SmellPolicy.resolve(candidates, 0, false, null);
        assertEquals(ids(wet), ids(dry));
        assertNull(dry.rainAfter());
    }

    @Test
    void oneTimeRainAfterCandidateDoesNotMaskOrGetMaskedByNormalDecay() {
        var observation = SmellPolicy.resolve(List.of(
            SmellPolicy.rainAfterCandidate("grass", 1),
            SmellPolicy.zombieCandidate("zombie", "z", 1),
            SmellPolicy.hotbarCandidate("bread")), 0, true, null);
        assertEquals(List.of("zombie"), ids(observation));
        assertEquals(8, observation.components().getFirst().effectiveStrength());
        assertEquals(1, observation.rainAfter().effectiveStrength());
    }

    @Test
    void rainAfterRequiresActualReachAndStillRespectsWeatherSuppression() {
        assertNull(SmellPolicy.resolve(List.of(), 0, true, null).rainAfter());
        assertNull(SmellPolicy.resolve(List.of(
            SmellPolicy.rainAfterCandidate("distant", 2)), 0, true, null).rainAfter());
        for (String reason : new String[] {"rain", "snow", "thunder", "submerged"}) {
            var observation = SmellPolicy.resolve(List.of(
                SmellPolicy.hotbarCandidate("lilac")), 0, true, reason);
            assertEquals("suppressed", observation.status());
            assertTrue(observation.components().isEmpty());
            assertNull(observation.rainAfter());
        }
    }

    @Test
    void inkOnlyEntersFromHotbarAndFlowersDoNotEnterAsDroppedItems() {
        assertEquals("ink_sac", SmellPolicy.hotbarCandidate("ink_sac").smellId());
        assertNull(SmellPolicy.droppedItemCandidate("ink_sac", "ink", 1.0));
        assertEquals("lilac", SmellPolicy.hotbarCandidate("lilac").smellId());
        assertNull(SmellPolicy.droppedItemCandidate("lilac", "flower", 1.0));
    }
    private static List<String> ids(SmellPolicy.Observation observation) {
        return observation.components().stream().map(SmellPolicy.Component::smellId).toList();
    }

    @Test
    void zombieOneWithFlowerOneMixesButZombieTwoMasksFlower() {
        var flower = SmellPolicy.hotbarCandidate("lily_of_the_valley");
        assertEquals(List.of("lily_of_the_valley", "zombie"), ids(SmellPolicy.resolve(
            List.of(flower, SmellPolicy.zombieCandidate("zombie", "z", 8)), 0, false, null)));
        assertEquals(List.of("zombie"), ids(SmellPolicy.resolve(
            List.of(flower, SmellPolicy.zombieCandidate("zombie", "z", 7)), 0, false, null)));
    }

    @Test
    void soupThreeMixesWithZombieOneToThreeButNotFour() {
        for (int score = 1; score <= 4; score++) {
            var observation = SmellPolicy.resolve(List.of(SmellPolicy.hotbarCandidate("mushroom_stew"),
                SmellPolicy.zombieCandidate("zombie", "z", 9 - score)), 0, false, null);
            assertEquals(score <= 3 ? List.of("soup", "zombie") : List.of("zombie"), ids(observation));
        }
    }

    @Test
    void ordinarySmellsKeepThreePointGapAndMaskAtFour() {
        var flower = SmellPolicy.hotbarCandidate("lily_of_the_valley");
        assertEquals(List.of("lilac", "lily_of_the_valley"), ids(SmellPolicy.resolve(List.of(flower,
            SmellPolicy.blockCandidate("lilac", "l", 2, false, false)), 0, false, null)));
        assertEquals(List.of("lilac"), ids(SmellPolicy.resolve(List.of(flower,
            SmellPolicy.blockCandidate("lilac", "l", 1, false, false)), 0, false, null)));
    }

    @Test
    void composterThreeAndHandheldFlowerOneCoexist() {
        var composter = SmellPolicy.blockCandidate("composter", "c", 1, true, false);
        assertEquals(3, composter.basePropagation());
        var observation = SmellPolicy.resolve(List.of(composter,
            SmellPolicy.hotbarCandidate("lily_of_the_valley")), 0, false, null);
        assertEquals(List.of("composter", "lily_of_the_valley"), ids(observation));
        assertEquals(List.of(3, 1), observation.components().stream().map(SmellPolicy.Component::effectiveStrength).toList());
    }

    @Test
    void flowerOutsideItsOwnReachDoesNotJoinSoup() {
        assertEquals(List.of("soup"), ids(SmellPolicy.resolve(List.of(
            SmellPolicy.hotbarCandidate("mushroom_stew"),
            SmellPolicy.blockCandidate("lily_of_the_valley", "flower", 2, false, false)), 0, false, null)));
    }

}
