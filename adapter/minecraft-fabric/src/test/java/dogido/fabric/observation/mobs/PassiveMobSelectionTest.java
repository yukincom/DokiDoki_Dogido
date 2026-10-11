package dogido.fabric.observation.mobs;

import java.util.List;
import java.util.stream.IntStream;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class PassiveMobSelectionTest {
    private record Mob(String id, String customName, boolean tamed) {}
    private static Mob wild(String id) { return new Mob(id, null, false); }
    private static List<Mob> candidates(List<Mob> observed) {
        return PassiveMobSelection.companions(observed, m -> m.tamed() || m.customName() != null);
    }

    @Test void namedCatDoesNotDisplaceWildlifeButRemainsAvailableForConversation() {
        List<Mob> wildlife = List.of(wild("cow1"), wild("chicken1"), wild("cow2"), wild("chicken2"));
        Mob cat = new Mob("cat", "クロちゃん", true);
        List<Mob> observed = java.util.stream.Stream.concat(wildlife.stream(), java.util.stream.Stream.of(cat)).toList();
        assertEquals(wildlife, PassiveMobSelection.regular(observed));
        assertEquals(List.of(cat), candidates(observed));
        assertEquals(cat, observed.get(4));
    }

    @Test void candidatesIncludeNamedWildAndUnnamedPetsWithoutDuplicatingRoutineEntries() {
        Mob named = new Mob("cow", "モモ", false), pet = new Mob("wolf", null, true);
        List<Mob> observed = List.of(named, wild("1"), wild("2"), wild("3"), pet,
            new Mob("cat1", "クロちゃん", false), new Mob("cat2", "クロちゃん", true), wild("4"));
        assertEquals(observed.subList(4, 7), candidates(observed));
        assertEquals(observed.subList(0, 4), PassiveMobSelection.regular(observed));
    }

    @Test void emptyAndSmallScansStayUnchangedAndCompanionStorageIsBounded() {
        assertEquals(List.of(), PassiveMobSelection.regular(List.of()));
        List<Mob> small = List.of(wild("1"), new Mob("cat", "クロちゃん", true));
        assertEquals(small, PassiveMobSelection.regular(small));
        assertTrue(candidates(small).isEmpty());
        List<Mob> many = IntStream.range(0, 40).mapToObj(i -> new Mob("cat"+i, "名前"+i, true)).toList();
        assertEquals(many.subList(4, 36), candidates(many));
    }
}
