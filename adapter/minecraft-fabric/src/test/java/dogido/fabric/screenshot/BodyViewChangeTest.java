package dogido.fabric.screenshot;
import java.awt.image.BufferedImage;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class BodyViewChangeTest {
    private BufferedImage image(int rgb) {var image=new BufferedImage(320,180,BufferedImage.TYPE_INT_RGB);for(int y=0;y<180;y++)for(int x=0;x<320;x++)image.setRGB(x,y,rgb);return image;}
    @Test void comparesExactlyFourBeforeAndKeepsFirstFour() {
        var changes=new BodyViewChange();for(int color:new int[]{0x101010,0x505050,0x909090,0xd0d0d0})assertTrue(changes.observe("body",1,image(color)).changed());
        var same=changes.observe("body",1,image(0x101010));assertTrue(same.compared());assertFalse(same.changed());
        assertTrue(changes.observe("body",1,image(0xffffff)).changed());
    }
    @Test void toleratesTinyAnimationButDetectsSubstantialVisualChange() {
        var changes=new BodyViewChange();for(int i=0;i<4;i++)changes.observe("body",1,image(0x808080));
        assertFalse(changes.observe("body",1,image(0x838383)).changed());
        var changed=image(0x808080);for(int y=0;y<90;y++)for(int x=0;x<160;x++)changed.setRGB(x,y,0xff0000);
        assertTrue(changes.observe("body",1,changed).changed());
    }
    @Test void movementWorldAndAspectChangesAlwaysCreateNewObservation() {
        for(int mode=0;mode<3;mode++) {
            var changes=new BodyViewChange();for(int i=0;i<4;i++)changes.observe("body",1,image(0));
            var result=changes.observe(mode==0?"new":"body",mode==1?2:1,mode==2?new BufferedImage(100,100,1):image(0));
            assertTrue(result.changed());assertFalse(result.compared());
        }
    }
}
