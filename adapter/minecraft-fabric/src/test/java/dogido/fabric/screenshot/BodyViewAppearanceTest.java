package dogido.fabric.screenshot;
import java.util.List;
import java.util.Set;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class BodyViewAppearanceTest {
    @Test void actualCategoryColorAndViewSectorSelectOneObject() {
        var flower=new BodyViewAppearance.Feature("b:flower","flower",Set.of("yellow","green"),.2,.6);
        var mob=new BodyViewAppearance.Feature("e:mob","mob",Set.of("yellow"),.8,.6);
        assertEquals("b:flower",BodyViewAppearance.select(List.of(flower,mob),"flower","yellow","left","any"));
        assertEquals("e:mob",BodyViewAppearance.select(List.of(flower,mob),"mob","any","right","any"));
        assertNull(BodyViewAppearance.select(List.of(flower,mob),"flower","blue","any","any"));
        assertNull(BodyViewAppearance.select(List.of(flower,mob),"any","yellow","any","any"));
        assertNull(BodyViewAppearance.select(List.of(flower,mob),"any","any","any","any"));
    }
    @Test void observedPixelColorsDoNotDependOnObjectNames() {
        assertEquals("yellow",BodyViewAppearance.color(0xffff00));
        assertEquals("red",BodyViewAppearance.color(0xff0000));
        assertEquals("blue",BodyViewAppearance.color(0x0000ff));
        assertEquals("black",BodyViewAppearance.color(0x000000));
        assertEquals("white",BodyViewAppearance.color(0xffffff));
    }
}
