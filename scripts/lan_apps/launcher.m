// The bundle owns its worker and its visible status window. Closing it stops that worker.
#import <Cocoa/Cocoa.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <unistd.h>

static int finderAlias(NSString *operation, NSString *file, NSString *app) {
    NSURL *alias = [NSURL fileURLWithPath:file];
    NSURL *target = [NSURL fileURLWithPath:app isDirectory:YES];
    NSError *error = nil;
    if ([operation isEqualToString:@"--write-alias"]) {
        NSData *data = [target bookmarkDataWithOptions:NSURLBookmarkCreationSuitableForBookmarkFile
                        includingResourceValuesForKeys:nil relativeToURL:nil error:&error];
        if (data && [NSURL writeBookmarkData:data toURL:alias options:0 error:&error]) {
            NSBundle *bundle = [NSBundle bundleWithURL:target];
            NSString *iconName = [bundle objectForInfoDictionaryKey:@"CFBundleIconFile"];
            NSURL *iconURL = iconName ? [bundle.resourceURL URLByAppendingPathComponent:iconName] : nil;
            NSImage *icon = iconURL ? [[NSImage alloc] initWithContentsOfURL:iconURL] : nil;
            if (icon && [[NSWorkspace sharedWorkspace] setIcon:icon forFile:file options:0]) return 0;
            fprintf(stderr, "Finderエイリアスにアイコンを設定できませんでした。\n");
        }
    } else {
        NSNumber *isAlias = nil;
        if (![alias getResourceValue:&isAlias forKey:NSURLIsAliasFileKey error:&error] || !isAlias.boolValue)
            return 1;
        NSURL *resolved = [NSURL URLByResolvingAliasFileAtURL:alias
                           options:NSURLBookmarkResolutionWithoutUI | NSURLBookmarkResolutionWithoutMounting
                           error:&error];
        struct stat resolvedFile, expectedFile;
        if (resolved && stat(resolved.fileSystemRepresentation, &resolvedFile) == 0 &&
                stat(target.fileSystemRepresentation, &expectedFile) == 0 &&
                resolvedFile.st_dev == expectedFile.st_dev && resolvedFile.st_ino == expectedFile.st_ino)
            return 0;
    }
    if (error) fprintf(stderr, "%s\n", error.localizedDescription.UTF8String);
    return 1;
}

@interface Launcher : NSObject <NSApplicationDelegate, NSWindowDelegate>
@property NSWindow *window;
@property NSTextView *output;
@property NSTask *task;
@property NSButton *runButton;
@property NSArray<NSButton *> *hostButtons;
@property NSTextField *messageField;
@property NSFileHandle *input;
@property NSFileHandle *reader;
@property NSMutableData *pending;
@property BOOL quitting;
@end

@implementation Launcher
- (void)append:(NSString *)s {
    NSTextStorage *store = self.output.textStorage;
    [store replaceCharactersInRange:NSMakeRange(store.length, 0) withString:s];
    if (store.length > 64000) [store deleteCharactersInRange:NSMakeRange(0, store.length - 48000)];
    [self.output scrollRangeToVisible:NSMakeRange(store.length, 0)];
}
- (NSButton *)button:(NSString *)title action:(SEL)action frame:(NSRect)frame {
    NSButton *button = [NSButton buttonWithTitle:title target:self action:action];
    button.frame = frame;
    [self.window.contentView addSubview:button];
    return button;
}
- (void)applicationDidFinishLaunching:(NSNotification *)note {
    BOOL host = [[NSBundle.mainBundle objectForInfoDictionaryKey:@"DogidoRole"] isEqual:@"host"];
    CGFloat messageHeight = host ? 78 : 0;
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,660,420 + messageHeight)
             styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable
             backing:NSBackingStoreBuffered defer:NO];
    self.window.title = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleName"];
    self.window.delegate = self;
    NSScrollView *scroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(12,55 + messageHeight,636,353)];
    self.output = [[NSTextView alloc] initWithFrame:scroll.contentView.bounds];
    self.output.editable = NO;
    self.output.font = [NSFont monospacedSystemFontOfSize:12 weight:NSFontWeightRegular];
    scroll.documentView = self.output;
    scroll.hasVerticalScroller = YES;
    [self.window.contentView addSubview:scroll];
    self.runButton = [self button:@"停止" action:@selector(toggle:) frame:NSMakeRect(542,10,100,34)];
    if (host) {
        NSTextField *title = [NSTextField labelWithString:@"お母さんからの伝言"];
        title.frame = NSMakeRect(15,103,155,20);
        [self.window.contentView addSubview:title];
        NSTextField *hint = [NSTextField labelWithString:@"音声で入力するには、入力欄でMacの音声入力ショートカットを押します。"];
        hint.font = [NSFont systemFontOfSize:NSFont.smallSystemFontSize];
        hint.textColor = NSColor.secondaryLabelColor;
        hint.frame = NSMakeRect(175,103,470,18);
        [self.window.contentView addSubview:hint];
        self.messageField = [NSTextField textFieldWithString:@""];
        self.messageField.placeholderString = @"ドギドに伝えてほしいこと";
        self.messageField.accessibilityLabel = title.stringValue;
        self.messageField.usesSingleLineMode = YES;
        self.messageField.frame = NSMakeRect(14,65,515,28);
        [self.window.contentView addSubview:self.messageField];
        self.window.initialFirstResponder = self.messageField;
        NSButton *recipient = [self button:@"伝言の宛先…" action:@selector(recipient:) frame:NSMakeRect(288,10,132,34)];
        recipient.toolTip = @"この画面とLINEからの伝言を届ける、登録済みのMacを選びます。";
        self.hostButtons = @[
            [self button:@"端末を追加" action:@selector(pair:) frame:NSMakeRect(12,10,128,34)],
            [self button:@"登録を解除" action:@selector(revoke:) frame:NSMakeRect(146,10,128,34)],
            recipient, [self button:@"送信" action:@selector(sendMessage:) frame:NSMakeRect(542,62,100,34)]];
        for (NSButton *button in self.hostButtons) button.enabled = NO;
    }
    NSMenu *menu = [NSMenu new];
    NSMenuItem *root = [NSMenuItem new];
    [menu addItem:root];
    root.submenu = [NSMenu new];
    [root.submenu addItemWithTitle:@"終了" action:@selector(terminate:) keyEquivalent:@"q"];
    if (host) {
        NSMenu *edit = [[NSMenu alloc] initWithTitle:@"編集"];
        NSMenuItem *editItem = [menu addItemWithTitle:@"編集" action:nil keyEquivalent:@""];
        editItem.submenu = edit;
        [edit addItemWithTitle:@"カット" action:@selector(cut:) keyEquivalent:@"x"];
        [edit addItemWithTitle:@"コピー" action:@selector(copy:) keyEquivalent:@"c"];
        [edit addItemWithTitle:@"ペースト" action:@selector(paste:) keyEquivalent:@"v"];
        [edit addItemWithTitle:@"すべて選択" action:@selector(selectAll:) keyEquivalent:@"a"];
    }
    NSApp.mainMenu = menu;
    [self.window center];
    [self.window makeKeyAndOrderFront:nil];
    [NSApp activateIgnoringOtherApps:YES];
    [self start];
}
- (void)start {
    NSDictionary *config = [NSDictionary dictionaryWithContentsOfURL:
        [NSBundle.mainBundle URLForResource:@"launch" withExtension:@"plist"]];
    NSString *resources = NSBundle.mainBundle.resourcePath;
    BOOL host = [[NSBundle.mainBundle objectForInfoDictionaryKey:@"DogidoRole"] isEqual:@"host"];
    NSString *python = config[@"python"];
    NSString *kit = config[@"kit"];
    if (!python.length || (!host && !kit.length) || ![NSFileManager.defaultManager isExecutableFileAtPath:python]) {
        [self append:@"起動に必要なPython環境を確認できません。導入セットの初期設定を確認してください。\n"];
        self.runButton.title = @"再起動";
        return;
    }
    NSArray *args = host
        ? @[@"-u", [resources stringByAppendingPathComponent:@"lan/connection.py"], @"host"]
        : @[@"-u", [resources stringByAppendingPathComponent:@"client_tools/start_client.py"], kit,
            @"--lan-tools", [resources stringByAppendingPathComponent:@"lan"]];
    NSTask *task = [NSTask new];
    task.executableURL = [NSURL fileURLWithPath:python];
    task.arguments = args;
    task.currentDirectoryURL = [NSURL fileURLWithPath:host ? resources : kit];
    NSMutableDictionary *env = [NSProcessInfo.processInfo.environment mutableCopy];
    env[@"PYTHONUNBUFFERED"] = @"1";
    env[@"PYTHONDONTWRITEBYTECODE"] = @"1";
    env[@"PYTHONIOENCODING"] = @"utf-8";
    if (!host) {
        env[@"DOGIDO_CLIENT_KIT"] = kit;
        env[@"DOGIDO_CLIENT_APP_BINARY"] = [resources stringByAppendingPathComponent:@"runtime/dogido-rust"];
    }
    task.environment = env;
    NSPipe *out = [NSPipe pipe], *in = [NSPipe pipe];
    task.standardOutput = out;
    task.standardError = out;
    task.standardInput = in;
    [self append:@"ドギドを起動します…\n"];
    NSError *error = nil;
    if (![task launchAndReturnError:&error]) {
        [self append:[NSString stringWithFormat:@"起動できませんでした。%@\n", error.localizedDescription]];
        self.runButton.title = @"再起動";
        return;
    }
    self.task = task;
    self.input = in.fileHandleForWriting;
    self.reader = out.fileHandleForReading;
    self.pending = [NSMutableData new];
    // Read on the main run loop, preserving UTF-8 sequences split between pipe reads.
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(pipeReady:)
        name:NSFileHandleDataAvailableNotification object:self.reader];
    [self.reader waitForDataInBackgroundAndNotify];
    self.runButton.title = @"停止";
    self.runButton.enabled = YES;
    for (NSButton *button in self.hostButtons) button.enabled = YES;
    task.terminationHandler = ^(NSTask *ended){
        dispatch_async(dispatch_get_main_queue(), ^{
            [self finished:ended];
        });
    };
    // An immediate exec/validation failure can occur before the handler was attached.
    if (!task.running) [self finished:task];
}
- (void)displayData:(NSData *)data final:(BOOL)final {
    [self.pending appendData:data];
    NSUInteger size = self.pending.length;
    const unsigned char *bytes = self.pending.bytes;
    NSUInteger end = size;
    if (!final && size) {
        NSUInteger pos = size - 1;
        while (pos && size-pos < 4 && (bytes[pos] & 0xc0) == 0x80) pos--;
        NSUInteger expected = bytes[pos] >= 0xf0 ? 4 : bytes[pos] >= 0xe0 ? 3 : bytes[pos] >= 0xc0 ? 2 : 1;
        if (size-pos < expected) end = pos;
    }
    if (end) {
        NSString *line = [[NSString alloc] initWithBytes:bytes length:end encoding:NSUTF8StringEncoding];
        [self append:line ?: @"[ログの文字コードを読み取れません]\n"];
        [self.pending replaceBytesInRange:NSMakeRange(0,end) withBytes:NULL length:0];
    }
}
- (void)pipeReady:(NSNotification *)notice {
    if (notice.object != self.reader) return;
    NSData *data = self.reader.availableData;
    [self displayData:data final:data.length == 0];
    if (data.length) [self.reader waitForDataInBackgroundAndNotify];
}
- (void)finished:(NSTask *)task {
    if (self.task != task) return;
    [[NSNotificationCenter defaultCenter] removeObserver:self name:NSFileHandleDataAvailableNotification object:self.reader];
    // A crashed supervisor might leave a separate worker holding stdout. Drain
    // available bytes; closing the app must never wait for such a worker's EOF.
    int fd = self.reader.fileDescriptor;
    fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) | O_NONBLOCK);
    char bytes[8192];
    ssize_t size;
    while ((size = read(fd, bytes, sizeof(bytes))) > 0)
        [self displayData:[NSData dataWithBytes:bytes length:(NSUInteger)size] final:NO];
    [self displayData:[NSData data] final:YES];
    [self.reader closeFile];
    [self.input closeFile];
    self.reader = nil;
    self.input = nil;
    self.task = nil;
    [self append:[NSString stringWithFormat:@"ドギドが終了しました（終了コード %d）。\n", task.terminationStatus]];
    self.runButton.title = @"再起動";
    self.runButton.enabled = YES;
    for (NSButton *button in self.hostButtons) button.enabled = NO;
    if (self.quitting) [NSApp replyToApplicationShouldTerminate:YES];
}
- (void)command:(NSString *)command {
    if (!self.task.running) return;
    NSError *error = nil;
    if (![self.input writeData:[command dataUsingEncoding:NSUTF8StringEncoding] error:&error])
        [self append:[error.localizedDescription stringByAppendingString:@"\n"]];
}
- (void)pair:(id)sender { [self command:@"pair\n"]; }
- (void)revoke:(id)sender { [self command:@"revoke\n"]; }
- (void)recipient:(id)sender { [self command:@"recipient\n"]; }
- (void)sendMessage:(id)sender {
    NSString *text = [self.messageField.stringValue stringByTrimmingCharactersInSet:
                      NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (!text.length) {
        [self.window makeFirstResponder:self.messageField];
        return;
    }
    // 共有側の上限と同じUnicodeスカラー数を数える。絵文字をUTF-16の2文字として数えない。
    if ([text lengthOfBytesUsingEncoding:NSUTF32LittleEndianStringEncoding] / 4 > 500) {
        [self append:@"伝言は500文字以内にしてください。\n"];
        return;
    }
    NSData *encoded = [NSJSONSerialization dataWithJSONObject:text options:NSJSONWritingFragmentsAllowed error:nil];
    if (!encoded) {
        [self append:@"伝言の文字を読み取れませんでした。入力内容を確認してください。\n"];
        return;
    }
    // 本文に引用符・改行があっても、子プロセスへはコマンド一行だけを渡す。
    NSString *json = [[NSString alloc] initWithData:encoded encoding:NSUTF8StringEncoding];
    [self command:[NSString stringWithFormat:@"message %@\n", json]];
}
- (void)toggle:(id)sender {
    if (!self.task) { [self start]; return; }
    self.runButton.enabled = NO;
    [self append:@"起動した処理を停止しています…\n"];
    if (self.task.running) [self.task terminate];
}
- (BOOL)windowShouldClose:(NSWindow *)window { [NSApp terminate:nil]; return NO; }
- (NSApplicationTerminateReply)applicationShouldTerminate:(NSApplication *)app {
    if (!self.task.running) return NSTerminateNow;
    self.quitting = YES;
    [self.task terminate];
    return NSTerminateLater;
}
@end

// The signed entry in the ZIP resolves its kit on the receiving Mac. The
// installed client retains its own stable app identifier and desktop alias.
static BOOL isPlayKit(NSURL *folder) {
    for (NSString *name in @[@"00_setup.command", @"client_tools/lan_apps/install.py", @"manifest.json"])
        if (![NSFileManager.defaultManager fileExistsAtPath:[folder URLByAppendingPathComponent:name].path])
            return NO;
    return YES;
}

static int openKitEntry(void) {
    [NSApplication sharedApplication];
    [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
    [NSApp activateIgnoringOtherApps:YES];
    NSURL *kit = NSBundle.mainBundle.bundleURL.URLByDeletingLastPathComponent;
    if (!isPlayKit(kit)) {
        // Finder may isolate a downloaded app from its siblings on first open.
        NSOpenPanel *panel = [NSOpenPanel openPanel];
        panel.message = @"展開したドギドのフォルダ（00_setup.command がある場所）を選んでください。";
        panel.canChooseFiles = NO;
        panel.canChooseDirectories = YES;
        if ([panel runModal] != NSModalResponseOK) return 0;
        kit = panel.URL;
    }
    if (isPlayKit(kit)) {
        NSURL *installed = [NSURL fileURLWithPath:@"/Applications/ドギド.app" isDirectory:YES];
        NSDictionary *config = [NSDictionary dictionaryWithContentsOfURL:
            [installed URLByAppendingPathComponent:@"Contents/Resources/launch.plist"]];
        NSString *registered = config[@"kit"];
        if ([registered isKindOfClass:NSString.class] &&
            [registered.stringByResolvingSymlinksInPath isEqual:kit.path.stringByResolvingSymlinksInPath] &&
            [NSFileManager.defaultManager isExecutableFileAtPath:[kit URLByAppendingPathComponent:@"dogido-llm/bin/python"].path]) {
            if ([NSWorkspace.sharedWorkspace openURL:installed]) return 0;
        } else {
            NSAlert *initial = [NSAlert new];
            initial.messageText = @"ドギドの初期設定";
            initial.informativeText = @"Minecraftとランチャーを閉じてから「初期設定」を押してください。ターミナルが開いたらEnterで進みます。完了後はデスクトップの「ドギド」から遊べます。";
            [initial addButtonWithTitle:@"初期設定"];
            [initial addButtonWithTitle:@"キャンセル"];
            if ([initial runModal] != NSAlertFirstButtonReturn) return 0;
            if ([NSWorkspace.sharedWorkspace openFile:[kit URLByAppendingPathComponent:@"00_setup.command"].path
                                      withApplication:@"Terminal"]) return 0;
        }
    }
    NSAlert *error = [NSAlert new];
    error.messageText = @"ドギドを開けませんでした";
    error.informativeText = @"展開したフォルダにある 00_setup.command を開いてください。";
    [error runModal];
    return 1;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 4 && (!strcmp(argv[1],"--write-alias") || !strcmp(argv[1],"--check-alias")))
            return finderAlias(@(argv[1]), @(argv[2]), @(argv[3]));
        if ([[NSBundle.mainBundle objectForInfoDictionaryKey:@"DogidoRole"] isEqual:@"kit-entry"])
            return openKitEntry();
        NSApplication *application = [NSApplication sharedApplication];
        signal(SIGPIPE, SIG_IGN);
        __attribute__((objc_precise_lifetime)) Launcher *delegate = [Launcher new];
        application.delegate = delegate;
        [application setActivationPolicy:NSApplicationActivationPolicyRegular];
        [application run];
        (void)delegate;
        return 0;
    }
}
