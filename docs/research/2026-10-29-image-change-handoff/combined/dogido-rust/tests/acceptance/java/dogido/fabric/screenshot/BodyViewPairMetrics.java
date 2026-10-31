package dogido.fabric.screenshot;

import com.google.gson.*;
import java.awt.image.BufferedImage;
import java.lang.reflect.Method;
import java.nio.file.Path;
import java.util.*;
import javax.imageio.ImageIO;

/** 実撮影の前後画像を本体の32×18比較へ通し、変化率と領域を返す。画像は加工しない。実行時の4枚前選択・移動判定は再現せず、入力した画像対の差分だけを測る。 */
public final class BodyViewPairMetrics {
    private static final int W=32,H=18;
    private static final Method PIXELS;
    static {
        try {PIXELS=BodyViewChange.class.getDeclaredMethod("pixels",BufferedImage.class);PIXELS.setAccessible(true);}
        catch(Exception e) {throw new ExceptionInInitializerError(e);}
    }
    private static int[] grid(BufferedImage image)throws Exception {return (int[])PIXELS.invoke(null,image);}
    private static JsonObject compare(BufferedImage before,BufferedImage after)throws Exception {
        if((long)before.getWidth()*after.getHeight()!=(long)after.getWidth()*before.getHeight())
            throw new IllegalArgumentException("aspect_ratio_changed");
        BodyViewChange comparator=new BodyViewChange();
        for(int i=0;i<4;i++)comparator.observe("offline_pair",0,before);
        var result=comparator.observe("offline_pair",0,after);
        if(!result.compared())throw new IllegalStateException("pair_not_compared");
        int[] a=grid(before),b=grid(after);
        boolean[] mask=new boolean[W*H];double[] differences=new double[W*H];
        int changed=0;double mean=0;
        for(int i=0;i<W*H;i++) {
            double delta=0;for(int c=0;c<3;c++)delta+=Math.abs(a[i*3+c]-b[i*3+c]);
            differences[i]=delta/3.0;mean+=differences[i];
            mask[i]=differences[i]>24;if(mask[i])changed++;
        }
        if(Math.abs(mean/(W*H)-result.meanDifference())>1e-9
           ||Math.abs(changed/(double)(W*H)-result.changedFraction())>1e-9)
            throw new IllegalStateException("production_metric_mismatch");
        JsonObject out=new JsonObject();
        out.addProperty("grid_width",W);out.addProperty("grid_height",H);
        out.addProperty("mean_rgb_difference_0_255",result.meanDifference());
        out.addProperty("changed_cell_count",changed);
        out.addProperty("changed_area_percent",100*result.changedFraction());
        out.addProperty("legacy_threshold_crossed",result.changed());
        out.addProperty("cell_difference_threshold_exclusive",24);
        // 24/255以下の差は領域へ入れない。連結領域は4近傍。小領域を消す閾値はここでは採用せず、全て記録して見落としを評価できるようにする。
        boolean[] visited=new boolean[W*H];List<JsonObject> regions=new ArrayList<>();
        for(int start=0;start<mask.length;start++) {
            if(!mask[start]||visited[start])continue;
            ArrayDeque<Integer> queue=new ArrayDeque<>();queue.add(start);visited[start]=true;
            int minX=W,minY=H,maxX=0,maxY=0,n=0;double sum=0;
            while(!queue.isEmpty()) {
                int cell=queue.removeFirst(),x=cell%W,y=cell/W;n++;sum+=differences[cell];
                minX=Math.min(minX,x);maxX=Math.max(maxX,x);minY=Math.min(minY,y);maxY=Math.max(maxY,y);
                for(int[] step:new int[][]{{-1,0},{1,0},{0,-1},{0,1}}) {
                    int nx=x+step[0],ny=y+step[1];if(nx<0||nx>=W||ny<0||ny>=H)continue;
                    int next=ny*W+nx;if(mask[next]&&!visited[next]){visited[next]=true;queue.add(next);}
                }
            }
            JsonObject region=new JsonObject();region.addProperty("changed_cells",n);
            region.addProperty("changed_area_percent",100*n/(double)(W*H));
            region.addProperty("mean_rgb_difference_0_255",sum/n);
            JsonArray box=new JsonArray();box.add(minX/(double)W);box.add(minY/(double)H);
            box.add((maxX+1)/(double)W);box.add((maxY+1)/(double)H);region.add("bbox_xyxy_0_1",box);
            regions.add(region);
        }
        regions.sort((x,y)->Integer.compare(y.get("changed_cells").getAsInt(),x.get("changed_cells").getAsInt()));
        JsonArray regionsJson=new JsonArray();for(JsonObject region:regions)regionsJson.add(region);
        out.add("regions",regionsJson);return out;
    }
    public static void main(String[] args)throws Exception {
        JsonArray pairs=JsonParser.parseReader(new java.io.InputStreamReader(System.in,java.nio.charset.StandardCharsets.UTF_8)).getAsJsonArray();
        Map<String,BufferedImage> images=new HashMap<>();JsonArray output=new JsonArray();
        for(JsonElement element:pairs) {
            JsonObject pair=element.getAsJsonObject();String before=pair.get("before").getAsString(),after=pair.get("after").getAsString();
            for(String path:List.of(before,after))if(!images.containsKey(path)) {
                BufferedImage image=ImageIO.read(Path.of(path).toFile());
                if(image==null)throw new IllegalArgumentException("unreadable_image");images.put(path,image);
            }
            JsonObject row=compare(images.get(before),images.get(after));row.add("id",pair.get("id"));output.add(row);
        }
        System.out.println(output);
    }
}
