// Compile against the patched llama.cpp tools/server headers. Pass the path to a fresh test directory.
// 見込み: 発句取消とwriter完了の間は原本を保持できる。実際のflockと採取器の終了処理を一時ファイルで調べる。モデルは使用しない。
#include "server-context.h"
#include "server-task.h"
#include "server-common.h"
#include "log.h"
#undef LOG_WRN
#define LOG_WRN(...) std::fprintf(stderr, __VA_ARGS__)
#include "dogido-capture.h"

static void require(bool pass, const char * reason) {
    if (!pass) { throw std::runtime_error(reason); }
}
static json load(const std::filesystem::path & path) {
    std::ifstream f(path);
    return json::parse(std::string(std::istreambuf_iterator<char>(f), {}));
}

int main(int argc, char ** argv) {
    require(argc == 2, "need an empty temporary fixture directory");
    const auto base = std::filesystem::path(argv[1]);
    require(std::filesystem::is_directory(base) && std::filesystem::is_empty(base), "fixture is not empty");
    const std::string token = "2c338301-e0ca-4c2f-9a84-12b10bca5404";
    const std::string id = "fixture-original";
    const auto target = base/id;
    std::filesystem::create_directory(target);
    dogido_capture c;
    c.directory = target;
    require(c.writer.start(target,token,id), "writer could not start");
    const auto marker = base/"_requests"/(token+".json");
    // 合格: 応答を返す前でもtokenから同じ応答が引けるが、排他取得は失敗し、終了済みとは読めない。
    const auto before = load(marker);
    require(before.at("response_id")==id && before.at("writer_closed")==false,"early marker");
    const int other = ::open((target/".writer.lock").c_str(),O_RDONLY);
    require(other>=0 && ::flock(other,LOCK_EX|LOCK_NB)==-1 && errno==EWOULDBLOCK, "writer must exclude readers");

    // 合格: 同じtokenを別応答に使っても元の対応は変化せず、パス風tokenではマーカーを作らない。
    const auto duplicate = base/"fixture-another";
    std::filesystem::create_directory(duplicate);
    dogido_capture_writer competing;
    require(!competing.start(duplicate,token,"fixture-another") && load(marker)==before,"reused token");
    require(!competing.start(duplicate,"../escape","fixture-another") && !std::filesystem::exists(base/"escape.json"),"bad token");

    c.manifest={{"status","recording"},{"capture_request_id",token},{"writer_closed",false},
                {"writer_instance",c.writer.instance()},{"response_id",id},
                {"total_generated_tokens",0},{"positions",json::array()}};
    sha256_init(&c.digest);
    c.hidden.open(target/"hidden.f32",std::ios::binary);
    c.hidden.write("one",3);
    sha256_update(&c.digest,reinterpret_cast<const unsigned char *>("one"),3);
    c.finish("partial text","cancelled_or_error",false);
    const auto completed = load(marker), manifest = load(target/"manifest.json");
    // 合格: 取消の最終manifest・対応表が揃い、数値ファイルが閉じ、同じlockを獲得できる。
    require(!c.hidden.is_open() && ::flock(other,LOCK_EX|LOCK_NB)==0, "premature terminal marker");
    for (const char * field : {"capture_request_id","response_id","writer_instance","status","writer_closed"}) {
        require(completed.at(field)==manifest.at(field), "manifest/marker mismatch");
    }
    require(completed.at("status")=="cancelled" && completed.at("writer_closed")==true && manifest.at("hidden_bytes")==3,"final state");
    c.finish("later","stop",true);
    require(load(marker)==completed,"late reset changed terminal decision");
    ::close(other);

    // 合格: 確定書込みに失敗したとき、ロックが解放されても終了済みマーカーを作らない。
    dogido_capture failed;
    failed.directory=base/"fixture-interrupted";std::filesystem::create_directory(failed.directory);
    const std::string token2="d529cf64-4022-4a49-afd7-4e7e864ba0b4";
    require(failed.writer.start(failed.directory,token2,"fixture-interrupted"),"failed writer setup");
    failed.manifest=c.manifest;failed.manifest["writer_closed"]=false;
    sha256_init(&failed.digest);failed.hidden.open(failed.directory/"hidden.f32",std::ios::binary);
    std::filesystem::create_directory(failed.directory/"manifest.tmp");
    failed.finish("","cancelled_or_error",false);
    require(load(base/"_requests"/(token2+".json")).at("writer_closed")==false,"I/O failure must hold");
    return 0;
}
