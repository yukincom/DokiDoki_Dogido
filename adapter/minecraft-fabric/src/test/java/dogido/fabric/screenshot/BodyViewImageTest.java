package dogido.fabric.screenshot;

import com.google.gson.*;
import java.awt.image.BufferedImage;
import java.io.ByteArrayInputStream;
import java.security.MessageDigest;
import java.util.Base64;
import java.util.HexFormat;
import java.util.Random;
import javax.imageio.ImageIO;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class BodyViewImageTest {
    private JsonObject metadata(String status) {JsonObject m=new JsonObject();m.addProperty("annotation_status",status);return m;}
    @Test void preservesAspectAndHashesTheActualTransmittedPng() throws Exception {
        BufferedImage source=new BufferedImage(1024,768,BufferedImage.TYPE_INT_RGB);
        var encoded=BodyViewImage.encode(source,metadata("owner_not_in_render_list"));
        assertEquals(192,encoded.width());assertEquals(144,encoded.height());assertArrayEquals(encoded.png(),Base64.getDecoder().decode(encoded.base64()));
        assertEquals(encoded.sha256(),HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(encoded.png())));
        BufferedImage decoded=ImageIO.read(new ByteArrayInputStream(encoded.png()));assertEquals(192,decoded.getWidth());assertEquals(0,decoded.getRGB(90,90)&0xffffff);
    }
    @Test void projectsTheSameFrameBoxAfterResizingAndDoesNotLabelAnOccludedOwner() throws Exception {
        BufferedImage source=new BufferedImage(384,192,BufferedImage.TYPE_INT_RGB);JsonObject m=metadata("available"),box=new JsonObject();
        box.addProperty("left",41);box.addProperty("top",41);box.addProperty("right",119);box.addProperty("bottom",159);m.add("player_box",box);
        var encoded=BodyViewImage.encode(source,m);BufferedImage decoded=ImageIO.read(new ByteArrayInputStream(encoded.png()));
        assertEquals(0x28ff50,decoded.getRGB(20,20)&0xffffff);assertEquals(0x28ff50,decoded.getRGB(60,80)&0xffffff);
        assertEquals(0,decoded.getRGB(21,21)&0xffffff);assertEquals(0x28ff50,decoded.getRGB(20,13)&0xffffff);
        m.addProperty("annotation_status","owner_occluded_or_unavailable");
        decoded=ImageIO.read(new ByteArrayInputStream(BodyViewImage.encode(source,m).png()));assertEquals(0,decoded.getRGB(20,20)&0xffffff);
    }
    @Test void maximumEntropyRgbImageFitsWireBoundAndPortraitRemainsPortrait() throws Exception {
        BufferedImage source=new BufferedImage(192,192,BufferedImage.TYPE_INT_RGB);Random random=new Random(42);
        for(int y=0;y<192;y++)for(int x=0;x<192;x++)source.setRGB(x,y,random.nextInt());
        var encoded=BodyViewImage.encode(source,metadata("owner_invisible"));assertTrue(encoded.png().length<=BodyViewProtocol.MAX_PNG_BYTES);
        var portrait=BodyViewImage.encode(new BufferedImage(120,800,BufferedImage.TYPE_INT_RGB),metadata("owner_not_in_render_list"));
        assertEquals(29,portrait.width());assertEquals(192,portrait.height());
    }
    @Test void rejectsUntrustedNonfiniteBoxInsteadOfDrawingAnInventedMarker() {
        JsonObject m=metadata("available"),box=new JsonObject();box.addProperty("left",Double.NaN);box.addProperty("top",0);box.addProperty("right",100);box.addProperty("bottom",100);m.add("player_box",box);
        assertThrows(IllegalArgumentException.class,()->BodyViewImage.encode(new BufferedImage(192,192,BufferedImage.TYPE_INT_RGB),m));
    }
}
