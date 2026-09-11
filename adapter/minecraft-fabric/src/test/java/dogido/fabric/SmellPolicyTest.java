package dogido.fabric;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.List;

import org.junit.jupiter.api.Test;

final class SmellPolicyTest {
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
            SmellPolicy.resolve(List.of(zombie), 0, false, null).smellId()
        );
        assertEquals(
            "none",
            SmellPolicy.resolve(List.of(zombie), -7, false, null).status()
        );
        SmellPolicy.Candidate adjacent = SmellPolicy.zombieCandidate("zombie", "z1", 1.0);
        assertEquals(
            1,
            SmellPolicy.resolve(List.of(adjacent), -7, false, null).effectiveStrength()
        );

        SmellPolicy.Candidate warmDistance = SmellPolicy.zombieCandidate("zombie", "z1", 10.0);
        assertEquals(
            "zombie",
            SmellPolicy.resolve(List.of(warmDistance), 2, false, null).smellId()
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
        assertEquals("rotten_flesh", winner.smellId());

        SmellPolicy.Observation touchingTie = SmellPolicy.resolve(
            List.of(
                SmellPolicy.zombieCandidate("zombie", "z1", 1.0),
                SmellPolicy.hotbarCandidate("rotten_flesh")
            ),
            0,
            false,
            null
        );
        assertEquals("decay", touchingTie.smellId());
        assertEquals("category", touchingTie.specificity());
        assertFalse(touchingTie.isSpecificZombie());
    }

    @Test
    void sameSmellDoesNotAccumulateAndCategoryTiesCollapse() {
        SmellPolicy.Candidate breadA = SmellPolicy.hotbarCandidate("bread");
        SmellPolicy.Candidate breadB = SmellPolicy.droppedItemCandidate("bread", "item-2", 1.0);
        SmellPolicy.Observation same = SmellPolicy.resolve(
            List.of(breadA, breadB),
            0,
            false,
            null
        );
        assertEquals("bread", same.smellId());
        assertEquals(3, same.effectiveStrength());

        SmellPolicy.Observation foodTie = SmellPolicy.resolve(
            List.of(
                SmellPolicy.hotbarCandidate("bread"),
                SmellPolicy.hotbarCandidate("cookie")
            ),
            0,
            false,
            null
        );
        assertEquals("food", foodTie.smellId());
        assertEquals("category", foodTie.specificity());
    }

    @Test
    void crossCategoryTieBecomesUnidentifiedMixture() {
        SmellPolicy.Observation observation = SmellPolicy.resolve(
            List.of(
                SmellPolicy.hotbarCandidate("bread"),
                SmellPolicy.biomeCandidate("swamp")
            ),
            0,
            false,
            null
        );
        assertEquals("mixed", observation.smellId());
        assertEquals("mixed", observation.category());
        assertEquals("mixed", observation.specificity());
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
        assertEquals("cooking_meat", cold.smellId());
        assertEquals(1, cold.effectiveStrength());

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
        assertEquals(1, dry.effectiveStrength());
        assertEquals(2, wet.effectiveStrength());
        assertEquals("rain_after", wet.smellId());

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
        assertEquals("rain_after", earth.smellId());
        assertEquals(1, earth.effectiveStrength());
        assertTrue(earth.rainAfterActive());
    }

    @Test
    void inkOnlyEntersFromHotbarAndFlowersDoNotEnterAsDroppedItems() {
        assertEquals("ink_sac", SmellPolicy.hotbarCandidate("ink_sac").smellId());
        assertNull(SmellPolicy.droppedItemCandidate("ink_sac", "ink", 1.0));
        assertEquals("lilac", SmellPolicy.hotbarCandidate("lilac").smellId());
        assertNull(SmellPolicy.droppedItemCandidate("lilac", "flower", 1.0));
    }
}
