const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const photo=fs.readFileSync(__dirname+'/photo-library.js','utf8');
function scope(window){const s={window};vm.createContext(s);vm.runInContext(photo.slice(photo.indexOf('  function photoFolderButton(){'),photo.indexOf('  function pageTarget(){')),s);return s;}
test('browser preview disables Finder button without claiming to import photos',()=>{
  const s=scope({});const html=s.photoFolderButton();assert.match(html,/data-show-photo-folder/);assert.match(html,/ disabled>/);assert.match(html,/Mac版の本棚アプリ/);assert.doesNotThrow(()=>s.showPhotoFolder());
});
test('Mac button posts only a fixed action, never a file path',()=>{
  const calls=[],s=scope({webkit:{messageHandlers:{dogidoPhotoFolder:{postMessage:value=>calls.push(value)}}}});
  const html=s.photoFolderButton();assert.doesNotMatch(html,/ disabled>/);assert.match(html,/写真の移動・取り込みはしません/);assert.match(html,/写真の場所をFinderで表示/);
  s.showPhotoFolder();assert.deepEqual(calls,['showMinecraftScreenshots']);
});
test('Finder action is available for an empty album and exits before editing actions',()=>{
  assert.match(photo,/\$\{photoFolderButton\(\)\}<h2 class="album-list-heading"/);
  assert.match(photo,/if\(button\.hasAttribute\('data-show-photo-folder'\)\)\{showPhotoFolder\(\);return;\}/);
});
test('native bridge is restricted to the same webview, main frame and bookshelf origin',()=>{
  const source=fs.readFileSync(__dirname+'/macos/main.swift','utf8');
  const action=source.slice(source.indexOf('    func userContentController('),source.indexOf('    private func requestClose()'));
  for(const guard of ['message.webView === web','message.frameInfo.isMainFrame','message.body as? String == "showMinecraftScreenshots"','let port = baseURL?.port','origin.protocol == "http"','origin.host == "127.0.0.1"','origin.port == port'])assert.ok(action.includes(guard),guard);
  assert.match(action,/Library\/Application Support\/minecraft\/screenshots/);assert.match(action,/fileExists/);assert.match(action,/NSWorkspace\.shared\.open\(folder\)/);
  assert.doesNotMatch(action,/createDirectory|moveItem|copyItem|removeItem|Process\(/);
});
