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
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,660,420)
             styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable
             backing:NSBackingStoreBuffered defer:NO];
    self.window.title = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleName"];
    self.window.delegate = self;
    NSScrollView *scroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(12,55,636,353)];
    self.output = [[NSTextView alloc] initWithFrame:scroll.contentView.bounds];
    self.output.editable = NO;
    self.output.font = [NSFont monospacedSystemFontOfSize:12 weight:NSFontWeightRegular];
    scroll.documentView = self.output;
    scroll.hasVerticalScroller = YES;
    [self.window.contentView addSubview:scroll];
    self.runButton = [self button:@"停止" action:@selector(toggle:) frame:NSMakeRect(542,10,100,34)];
    if ([[NSBundle.mainBundle objectForInfoDictionaryKey:@"DogidoRole"] isEqual:@"host"]) {
        self.hostButtons = @[
            [self button:@"端末を追加" action:@selector(pair:) frame:NSMakeRect(12,10,128,34)],
            [self button:@"登録を解除" action:@selector(revoke:) frame:NSMakeRect(146,10,128,34)]];
    }
    NSMenu *menu = [NSMenu new];
    NSMenuItem *root = [NSMenuItem new];
    [menu addItem:root];
    root.submenu = [NSMenu new];
    [root.submenu addItemWithTitle:@"終了" action:@selector(terminate:) keyEquivalent:@"q"];
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

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 4 && (!strcmp(argv[1],"--write-alias") || !strcmp(argv[1],"--check-alias")))
            return finderAlias(@(argv[1]), @(argv[2]), @(argv[3]));
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
