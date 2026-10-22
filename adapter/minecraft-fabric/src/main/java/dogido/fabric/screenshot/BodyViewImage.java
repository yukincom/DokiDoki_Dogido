package dogido.fabric.screenshot;

import com.google.gson.JsonObject;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Base64;
import java.util.HexFormat;
import javax.imageio.ImageIO;

/** raw画像と同frameの投影枠から長辺192pxを作る純粋変換。意味認識はせず、IO・通信・retentionは呼出側が所有する。 */
public final class BodyViewImage {
    public static final int EDGE=192;
    private static final int GREEN=0x28ff50;
    private BodyViewImage() {}
    public record Encoded(byte[] png,String base64,String sha256,int width,int height) {}

    /** Lanczos3を横・縦に適用し、最後に同じ縮尺でPLAYER枠を重ねてRGB PNGにする。 */
    public static Encoded encode(BufferedImage source,JsonObject metadata) throws IOException {
        if(source==null || source.getWidth()<1 || source.getHeight()<1
                || (long)source.getWidth()*source.getHeight()>16_777_216L) throw new IllegalArgumentException("image_size");
        double ratio=Math.min(1,(double)EDGE/Math.max(source.getWidth(),source.getHeight()));
        int width=Math.max(1,(int)Math.round(source.getWidth()*ratio)),height=Math.max(1,(int)Math.round(source.getHeight()*ratio));
        BufferedImage small=resize(source,width,height);
        if(metadata.has("annotation_status") && "available".equals(metadata.get("annotation_status").getAsString())) {
            JsonObject box=metadata.getAsJsonObject("player_box");
            if(box==null) throw new IllegalArgumentException("player_box");
            int left=scaled(box,"left",(double)width/source.getWidth(),width,false),right=scaled(box,"right",(double)width/source.getWidth(),width,true);
            int top=scaled(box,"top",(double)height/source.getHeight(),height,false),bottom=scaled(box,"bottom",(double)height/source.getHeight(),height,true);
            if(right<left || bottom<top) throw new IllegalArgumentException("player_box");
            for(int x=left;x<=right;x++) {small.setRGB(x,top,GREEN);small.setRGB(x,bottom,GREEN);}
            for(int y=top;y<=bottom;y++) {small.setRGB(left,y,GREEN);small.setRGB(right,y,GREEN);}
            label(small,Math.min(left,Math.max(0,width-36)),top>=7?top-7:Math.min(height-5,top+2));
        }
        ByteArrayOutputStream bytes=new ByteArrayOutputStream();
        if(!ImageIO.write(small,"png",bytes)) throw new IOException("png_encoder_unavailable");
        byte[] png=bytes.toByteArray();if(png.length>BodyViewProtocol.MAX_PNG_BYTES) throw new IOException("image_too_large");
        try { return new Encoded(png,Base64.getEncoder().encodeToString(png),HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(png)),width,height); }
        catch(NoSuchAlgorithmException impossible) {throw new IllegalStateException(impossible);}
    }
    private static int scaled(JsonObject box,String key,double scale,int limit,boolean upper) {
        double value=box.get(key).getAsDouble()*scale;
        if(!Double.isFinite(value))throw new IllegalArgumentException(key);
        return Math.max(0,Math.min(limit-1,(int)(upper?Math.ceil(value):Math.floor(value))));
    }
    private record Weights(int first,double[] values) {}
    private static Weights[] weights(int source,int target) {
        Weights[] all=new Weights[target];double scale=Math.max(1,(double)source/target),radius=3*scale;
        for(int i=0;i<target;i++) {
            double center=(i+.5)*source/target-.5;
            int first=Math.max(0,(int)Math.ceil(center-radius)),last=Math.min(source-1,(int)Math.floor(center+radius));
            double[] w=new double[last-first+1];double total=0;
            for(int j=0;j<w.length;j++) {double x=(first+j-center)/scale;w[j]=sinc(x)*sinc(x/3);total+=w[j];}
            for(int j=0;j<w.length;j++)w[j]/=total;all[i]=new Weights(first,w);
        }
        return all;
    }
    private static double sinc(double x) {return Math.abs(x)<1e-8?1:Math.sin(Math.PI*x)/(Math.PI*x);}
    private static BufferedImage resize(BufferedImage input,int width,int height) {
        Weights[] horizontal=weights(input.getWidth(),width),vertical=weights(input.getHeight(),height);
        float[] middle=new float[width*input.getHeight()*3];
        for(int y=0;y<input.getHeight();y++)for(int x=0;x<width;x++) {
            Weights w=horizontal[x];int offset=(y*width+x)*3;
            for(int j=0;j<w.values.length;j++) {
                int rgb=input.getRGB(w.first+j,y);
                for(int c=0;c<3;c++)middle[offset+c]+=((rgb>>(16-c*8))&255)*w.values[j];
            }
        }
        BufferedImage result=new BufferedImage(width,height,BufferedImage.TYPE_INT_RGB);
        for(int y=0;y<height;y++)for(int x=0;x<width;x++) {
            Weights w=vertical[y];int rgb=0;
            for(int c=0;c<3;c++) {double channel=0;
                for(int j=0;j<w.values.length;j++)channel+=middle[((w.first+j)*width+x)*3+c]*w.values[j];
                rgb=(rgb<<8)|Math.max(0,Math.min(255,(int)Math.round(channel)));
            }
            result.setRGB(x,y,rgb);
        }
        return result;
    }
    /** JDKフォント環境に依存しない5x5 glyph。枠が表示不能の時はラベルも描かない。 */
    private static void label(BufferedImage image,int x,int y) {
        String[] glyphs={"1111010001111101000010000","1000010000100001000011111","0111010001111111000110001",
            "1000110001010100010000100","1111110000111101000011111","1111010001111101010010010"};
        for(int g=0;g<glyphs.length;g++)for(int row=0;row<5;row++)for(int col=0;col<5;col++) {
            int px=x+g*6+col,py=y+row;
            if(px>=0 && px<image.getWidth() && py>=0 && py<image.getHeight())image.setRGB(px,py,glyphs[g].charAt(row*5+col)=='1'?GREEN:0);
        }
    }
}
