package dogido.fabric;

import java.util.List;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class PassiveMobSelectionTest {
    private record Mob(String id, String customName) {}
    private static Mob unnamed(String id) { return new Mob(id, null); }
    private static List<Mob> select(List<Mob> observed) {
        return PassiveMobSelection.select(observed, mob -> mob.customName() != null);
    }

    @Test void fourWildAnimalsCannotDisplaceTheNamedCat() {
        Mob cow = unnamed("cow1"), chicken = unnamed("chicken1"), otherCow = unnamed("cow2");
        Mob otherChicken = unnamed("chicken2"), cat = new Mob("cat1", "クロちゃん");
        List<Mob> observed = List.of(cow, chicken, otherCow, otherChicken, cat);
        assertEquals(List.of(cow, chicken, otherCow, cat), select(observed));
        assertEquals(List.of(cow, chicken, otherCow, otherChicken, cat), observed);
    }

    @Test void multipleNamedIndividualsAreRetainedInTheirOriginalEncounterOrder() {
        Mob wild = unnamed("cow1"), namedWild = new Mob("cow2", "モモ"), other = unnamed("chicken1");
        Mob cat1 = new Mob("cat1", "クロちゃん"), cat2 = new Mob("cat2", "クロちゃん");
        assertEquals(List.of(wild, namedWild, cat1, cat2),
            select(List.of(wild, namedWild, other, cat1, cat2)));
    }

    @Test void unnamedAndSmallObservationsKeepTheirExistingOrderAndBound() {
        List<Mob> wild = List.of(unnamed("1"), unnamed("2"), unnamed("3"), unnamed("4"), unnamed("5"));
        assertEquals(wild.subList(0, 4), select(wild));
        assertEquals(List.of(), select(List.of()));
        List<Mob> small = List.of(wild.get(0), new Mob("cat", "クロちゃん"));
        assertEquals(small, select(small));
        List<Mob> named = List.of(new Mob("1", "a"), new Mob("2", "b"), new Mob("3", "c"),
            new Mob("4", "d"), new Mob("5", "e"));
        assertEquals(named.subList(0, 4), select(named));
    }
}
