// The bundle owns its worker and its visible status window. Closing it stops that worker.
#import <Cocoa/Cocoa.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <unistd.h>

// 共有画面だけの配色。ターミナルの白・茶・緑・水色を、角のある枠とボタンへ使う。
static NSColor *hostColor(unsigned rgb) {
    return [NSColor colorWithSRGBRed:((rgb >> 16) & 255) / 255.0
                              green:((rgb >> 8) & 255) / 255.0 blue:(rgb & 255) / 255.0 alpha:1];
}
static NSColor *hostInk(void) { return hostColor(0x594010); }
static void fillHostRect(NSRect frame, unsigned rgb) {
    [hostColor(rgb) setFill];
    NSRectFill(frame);
}
static void hostBevel(NSRect frame, BOOL pressed, unsigned face) {
    fillHostRect(frame, 0x665737);
    NSRect inner = NSInsetRect(frame, 2, 2);
    fillHostRect(inner, face);
    fillHostRect(NSMakeRect(NSMinX(inner), NSMaxY(inner)-3, inner.size.width, 3), pressed ? 0xb2ad96 : 0xffffff);
    fillHostRect(NSMakeRect(NSMinX(inner), NSMinY(inner), 3, inner.size.height), pressed ? 0xb2ad96 : 0xffffff);
    fillHostRect(NSMakeRect(NSMinX(inner)+3, NSMinY(inner), inner.size.width-3, 3), pressed ? 0xffffff : 0xb2ad96);
    fillHostRect(NSMakeRect(NSMaxX(inner)-3, NSMinY(inner), 3, inner.size.height-3), pressed ? 0xffffff : 0xb2ad96);
}

@interface HostCanvas : NSView
@end
@implementation HostCanvas
- (void)drawRect:(NSRect)dirty {
    fillHostRect(self.bounds, 0xf5f4ed);
    // 上端の草色と小さな段差だけに模様を留め、本文の背景は無地にする。
    CGFloat top = NSMaxY(self.bounds);
    fillHostRect(NSMakeRect(0, top-6, self.bounds.size.width, 6), 0x9bb58b);
    for (NSInteger x = 0; x < self.bounds.size.width; x += 48)
        fillHostRect(NSMakeRect(x, top-10, 20, 4), 0x9bb58b);
}
@end

@interface HostPanel : NSView
@property BOOL inset;
@end
@implementation HostPanel
- (void)drawRect:(NSRect)dirty { hostBevel(self.bounds, self.inset, self.inset ? 0xffffff : 0xeceadc); }
@end

@interface HostButtonCell : NSButtonCell
@property BOOL accent;
@end
@implementation HostButtonCell
- (void)drawBezelWithFrame:(NSRect)frame inView:(NSView *)view {
    unsigned face = !self.enabled ? 0xe7e5db : self.accent ? (self.highlighted ? 0xa6bd96 : 0xc2d5b2) : 0xeceadc;
    hostBevel(frame, self.highlighted, face);
}
- (NSRect)drawTitle:(NSAttributedString *)title withFrame:(NSRect)frame inView:(NSView *)view {
    NSMutableAttributedString *styled = [title mutableCopy];
    [styled addAttributes:@{NSForegroundColorAttributeName:self.enabled ? hostInk() : hostColor(0x9a9483),
                            NSFontAttributeName:[NSFont monospacedSystemFontOfSize:13 weight:NSFontWeightSemibold]}
                   range:NSMakeRange(0, styled.length)];
    return [super drawTitle:styled withFrame:frame inView:view];
}
@end

// フォーカス直後の空欄も、入力中も同じカーソル・選択色を使う。
@interface HostMessageCell : NSTextFieldCell
@end
@implementation HostMessageCell
- (NSText *)setUpFieldEditorAttributes:(NSText *)text {
    NSText *editor = [super setUpFieldEditorAttributes:text];
    if ([editor isKindOfClass:NSTextView.class]) {
        NSTextView *view = (NSTextView *)editor;
        view.insertionPointColor = hostColor(0x1cb4c0);
        view.selectedTextAttributes = @{NSBackgroundColorAttributeName:hostColor(0xb7d9ff), NSForegroundColorAttributeName:hostInk()};
    }
    return editor;
}
@end

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
@property NSDictionary *outputAttributes;
@property NSTask *task;
@property NSButton *runButton;
@property NSArray<NSButton *> *hostButtons;
@property NSTextField *messageField;
@property NSPanel *settingsWindow;
@property NSArray<NSButton *> *managementButtons;
@property NSTextField *administratorField;
@property NSTextField *settingsError;
@property NSString *administratorName;
@property NSMutableString *hostOutputBuffer;
@property NSArray<NSDictionary *> *registeredDevices;
@property NSMutableSet<NSString *> *checkedDevices;
@property NSString *recipientID;
@property NSScrollView *deviceScroll;
@property NSPopUpButton *recipientPicker;
@property NSButton *deleteButton;
@property NSFileHandle *input;
@property NSFileHandle *reader;
@property NSMutableData *pending;
@property BOOL quitting;
@end

@implementation Launcher
- (void)append:(NSString *)s {
    NSTextStorage *store = self.output.textStorage;
    if (self.outputAttributes)
        [store appendAttributedString:[[NSAttributedString alloc] initWithString:s attributes:self.outputAttributes]];
    else
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
- (NSButton *)hostButton:(NSString *)title action:(SEL)action frame:(NSRect)frame accent:(BOOL)accent {
    NSButton *button = [[NSButton alloc] initWithFrame:frame];
    HostButtonCell *cell = [HostButtonCell new];
    cell.accent = accent;
    cell.bezelStyle = NSBezelStyleRegularSquare;
    button.cell = cell;
    button.title = title;
    button.target = self;
    button.action = action;
    button.buttonType = NSButtonTypeMomentaryPushIn;
    button.bordered = YES;
    button.contentTintColor = hostInk();
    [self.window.contentView addSubview:button];
    return button;
}
- (void)buildHostInterface {
    self.hostOutputBuffer = [NSMutableString new];
    self.registeredDevices = @[];
    self.checkedDevices = [NSMutableSet new];
    NSDictionary *preferences = [NSDictionary dictionaryWithContentsOfURL:self.settingsFile];
    BOOL exists = [NSFileManager.defaultManager fileExistsAtPath:self.settingsFile.path];
    self.administratorName = exists ? [self checkedAdministratorName:preferences[@"administrator_name"]] : @"管理者";
    self.window.appearance = [NSAppearance appearanceNamed:NSAppearanceNameAqua];
    self.window.contentView = [[HostCanvas alloc] initWithFrame:NSMakeRect(0,0,780,590)];
    NSTextField *heading = [NSTextField labelWithString:@"ドギド共有"];
    heading.font = [NSFont monospacedSystemFontOfSize:22 weight:NSFontWeightBold];
    heading.textColor = hostInk();
    heading.frame = NSMakeRect(26,532,420,30);
    [self.window.contentView addSubview:heading];
    self.runButton = [self hostButton:@"停止" action:@selector(toggle:) frame:NSMakeRect(614,529,88,38) accent:NO];
    NSButton *settings = [self hostButton:@"" action:@selector(showSettings:) frame:NSMakeRect(714,529,42,38) accent:NO];
    settings.image = [[NSImage imageWithSystemSymbolName:@"gearshape.fill" accessibilityDescription:@"設定"]
        imageWithSymbolConfiguration:[NSImageSymbolConfiguration configurationWithPointSize:18 weight:NSFontWeightSemibold]];
    settings.imagePosition = NSImageOnly;
    settings.accessibilityLabel = @"設定";
    settings.toolTip = @"設定";
    HostPanel *logPanel = [[HostPanel alloc] initWithFrame:NSMakeRect(24,164,732,348)];
    logPanel.inset = YES;
    [self.window.contentView addSubview:logPanel];
    NSScrollView *scroll = [[NSScrollView alloc] initWithFrame:NSInsetRect(logPanel.frame, 5, 5)];
    self.output = [[NSTextView alloc] initWithFrame:scroll.contentView.bounds];
    self.output.editable = NO;
    self.output.font = [NSFont monospacedSystemFontOfSize:13 weight:NSFontWeightRegular];
    self.output.textColor = hostInk();
    self.outputAttributes = @{NSFontAttributeName:self.output.font, NSForegroundColorAttributeName:hostInk()};
    self.output.backgroundColor = NSColor.whiteColor;
    self.output.textContainerInset = NSMakeSize(12,12);
    self.output.selectedTextAttributes = @{NSBackgroundColorAttributeName:hostColor(0xb7d9ff), NSForegroundColorAttributeName:hostInk()};
    scroll.documentView = self.output;
    scroll.hasVerticalScroller = YES;
    [self.window.contentView addSubview:scroll];
    HostPanel *messagePanel = [[HostPanel alloc] initWithFrame:NSMakeRect(24,24,732,122)];
    [self.window.contentView addSubview:messagePanel];
    NSTextField *title = [NSTextField labelWithString:@"メッセージ"];
    title.font = [NSFont monospacedSystemFontOfSize:13 weight:NSFontWeightBold];
    title.textColor = hostInk();
    title.frame = NSMakeRect(42,112,680,20);
    [self.window.contentView addSubview:title];
    NSTextField *hint = [NSTextField labelWithString:@"音声で入力するには、入力欄でMacの音声入力ショートカットを押します。"];
    hint.font = [NSFont systemFontOfSize:11];
    hint.textColor = hostColor(0x807457);
    hint.frame = NSMakeRect(42,89,680,18);
    [self.window.contentView addSubview:hint];
    HostPanel *inputPanel = [[HostPanel alloc] initWithFrame:NSMakeRect(42,42,594,36)];
    inputPanel.inset = YES;
    [self.window.contentView addSubview:inputPanel];
    self.messageField = [NSTextField textFieldWithString:@""];
    self.messageField.cell = [[HostMessageCell alloc] initTextCell:@""];
    self.messageField.editable = YES;
    self.messageField.selectable = YES;
    self.messageField.placeholderString = @"ドギドに伝えてほしいこと";
    self.messageField.accessibilityLabel = title.stringValue;
    self.messageField.usesSingleLineMode = YES;
    self.messageField.bezeled = NO;
    self.messageField.drawsBackground = NO;
    self.messageField.focusRingType = NSFocusRingTypeNone;
    self.messageField.font = [NSFont monospacedSystemFontOfSize:14 weight:NSFontWeightRegular];
    self.messageField.textColor = hostInk();
    self.messageField.frame = NSMakeRect(51,50,576,21);
    // Cocoaのfield editorに変換確定を任せ、確定後のReturnだけを既存の送信へ渡す。
    // フォーカス移動では送信せず、ボタン・Enterとも同じ検査と入力欄クリアを使う。
    self.messageField.target = self;
    self.messageField.action = @selector(sendMessage:);
    self.messageField.cell.sendsActionOnEndEditing = NO;
    [self.window.contentView addSubview:self.messageField];
    self.window.initialFirstResponder = self.messageField;
    self.hostButtons = @[[self hostButton:@"送信" action:@selector(sendMessage:) frame:NSMakeRect(648,40,90,40) accent:YES]];
    for (NSButton *button in self.hostButtons) button.enabled = NO;
}
// 管理者名はこのアプリの設定が正本。送信時に名前を本文と一緒に確定し、受付済みの伝言へ遡及しない。
- (NSURL *)settingsFile {
    NSURL *root = [NSFileManager.defaultManager URLsForDirectory:NSApplicationSupportDirectory inDomains:NSUserDomainMask].firstObject;
    NSString *identifier = NSBundle.mainBundle.bundleIdentifier ?: @"com.dokidoki-dogido.lan.host";
    return [[root URLByAppendingPathComponent:identifier isDirectory:YES] URLByAppendingPathComponent:@"ui-settings.plist"];
}
- (NSString *)checkedAdministratorName:(id)value {
    if (![value isKindOfClass:NSString.class]) return nil;
    NSString *name = [value stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
    if (!name.length || [name lengthOfBytesUsingEncoding:NSUTF32LittleEndianStringEncoding] / 4 > 80 ||
            [name rangeOfCharacterFromSet:NSCharacterSet.controlCharacterSet].location != NSNotFound ||
            [name rangeOfCharacterFromSet:NSCharacterSet.newlineCharacterSet].location != NSNotFound) return nil;
    return name;
}
- (void)buildSettingsInterface {
    self.settingsWindow = [[NSPanel alloc] initWithContentRect:NSMakeRect(0,0,520,430)
            styleMask:NSWindowStyleMaskTitled backing:NSBackingStoreBuffered defer:NO];
    self.settingsWindow.title = @"設定";
    self.settingsWindow.appearance = self.window.appearance;
    self.settingsWindow.contentView = [[HostCanvas alloc] initWithFrame:NSMakeRect(0,0,520,430)];
    NSTextField *label = [NSTextField labelWithString:@"管理者名"];
    label.frame = NSMakeRect(24,380,472,20);
    label.font = [NSFont monospacedSystemFontOfSize:13 weight:NSFontWeightBold];
    label.textColor = hostInk();
    [self.settingsWindow.contentView addSubview:label];
    self.administratorField = [NSTextField textFieldWithString:@""];
    self.administratorField.cell = [[HostMessageCell alloc] initTextCell:@""];
    self.administratorField.editable = YES;
    self.administratorField.selectable = YES;
    self.administratorField.bezeled = YES;
    self.administratorField.drawsBackground = YES;
    self.administratorField.backgroundColor = NSColor.whiteColor;
    self.administratorField.textColor = hostInk();
    self.administratorField.font = [NSFont monospacedSystemFontOfSize:14 weight:NSFontWeightRegular];
    self.administratorField.usesSingleLineMode = YES;
    self.administratorField.frame = NSMakeRect(24,341,360,30);
    self.administratorField.accessibilityLabel = @"管理者名";
    self.administratorField.target = self;
    self.administratorField.action = @selector(saveSettings:);
    self.administratorField.cell.sendsActionOnEndEditing = NO;
    [self.settingsWindow.contentView addSubview:self.administratorField];
    self.settingsWindow.initialFirstResponder = self.administratorField;
    NSButton *save = [self hostButton:@"保存" action:@selector(saveSettings:) frame:NSMakeRect(398,339,98,34) accent:YES];
    [self.settingsWindow.contentView addSubview:save];
    NSTextField *registered = [NSTextField labelWithString:@"登録PC"];
    registered.frame = NSMakeRect(24,292,320,20);
    registered.font = label.font;
    registered.textColor = hostInk();
    [self.settingsWindow.contentView addSubview:registered];
    NSButton *add = [self hostButton:@"登録追加" action:@selector(pair:) frame:NSMakeRect(398,284,98,34) accent:NO];
    [self.settingsWindow.contentView addSubview:add];
    self.deviceScroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(24,134,472,140)];
    self.deviceScroll.hasVerticalScroller = YES;
    self.deviceScroll.borderType = NSBezelBorder;
    self.deviceScroll.backgroundColor = NSColor.whiteColor;
    [self.settingsWindow.contentView addSubview:self.deviceScroll];
    NSTextField *recipient = [NSTextField labelWithString:@"伝言の宛先"];
    recipient.frame = NSMakeRect(24,94,100,20);
    recipient.font = label.font;
    recipient.textColor = hostInk();
    [self.settingsWindow.contentView addSubview:recipient];
    self.recipientPicker = [[NSPopUpButton alloc] initWithFrame:NSMakeRect(132,86,250,32) pullsDown:NO];
    self.recipientPicker.target = self;
    self.recipientPicker.action = @selector(recipient:);
    self.recipientPicker.accessibilityLabel = @"伝言の宛先";
    self.recipientPicker.font = [NSFont systemFontOfSize:13];
    [self.settingsWindow.contentView addSubview:self.recipientPicker];
    self.deleteButton = [self hostButton:@"削除" action:@selector(revoke:) frame:NSMakeRect(398,86,98,34) accent:NO];
    [self.settingsWindow.contentView addSubview:self.deleteButton];
    self.managementButtons = @[add, self.deleteButton];
    self.settingsError = [NSTextField wrappingLabelWithString:@""];
    self.settingsError.frame = NSMakeRect(24,54,472,26);
    self.settingsError.font = [NSFont systemFontOfSize:11];
    self.settingsError.textColor = hostColor(0x984b39);
    [self.settingsWindow.contentView addSubview:self.settingsError];
    NSButton *close = [self hostButton:@"閉じる" action:@selector(closeSettings:) frame:NSMakeRect(398,16,98,34) accent:NO];
    [self.settingsWindow.contentView addSubview:close];
    [self refreshDeviceList];
}
// チェックは削除候補だけ。宛先と登録の正本はworkerに置き、一覧の更新で保存済み値を反映する。
- (void)refreshDeviceList {
    if (!self.deviceScroll) return;
    NSMutableSet *live = [NSMutableSet new];
    for (NSDictionary *device in self.registeredDevices) [live addObject:device[@"id"]];
    [self.checkedDevices intersectSet:live];
    CGFloat width = self.deviceScroll.contentSize.width;
    CGFloat height = MAX(self.deviceScroll.contentSize.height, self.registeredDevices.count * 34 + 12);
    NSView *list = [[NSView alloc] initWithFrame:NSMakeRect(0,0,width,height)];
    [self.recipientPicker removeAllItems];
    [self.recipientPicker addItemWithTitle:@"未選択"];
    self.recipientPicker.lastItem.enabled = NO;
    for (NSUInteger index = 0; index < self.registeredDevices.count; index++) {
        NSDictionary *device = self.registeredDevices[index];
        NSString *name = device[@"name"];
        NSButton *check = [NSButton checkboxWithTitle:name target:self action:@selector(checkDevice:)];
        check.frame = NSMakeRect(12,height-40-index*34,width-24,30);
        check.font = [NSFont systemFontOfSize:14];
        check.contentTintColor = hostInk();
        check.cell.lineBreakMode = NSLineBreakByTruncatingTail;
        check.tag = index;
        check.state = [self.checkedDevices containsObject:device[@"id"]] ? NSControlStateValueOn : NSControlStateValueOff;
        [list addSubview:check];
        // 同名PCもmenu itemのrepresentedObjectで区別し、表示名から登録IDを逆引きしない。
        NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:name action:nil keyEquivalent:@""];
        item.representedObject = device[@"id"];
        [self.recipientPicker.menu addItem:item];
        if ([self.recipientID isEqual:device[@"id"]]) [self.recipientPicker selectItem:item];
    }
    self.deviceScroll.documentView = list;
    [self setWorkerControlsEnabled:self.task.running];
}
- (void)checkDevice:(NSButton *)sender {
    if (sender.tag < 0 || (NSUInteger)sender.tag >= self.registeredDevices.count) return;
    NSString *device = self.registeredDevices[sender.tag][@"id"];
    if (sender.state == NSControlStateValueOn) [self.checkedDevices addObject:device];
    else [self.checkedDevices removeObject:device];
    self.deleteButton.enabled = self.task.running && self.checkedDevices.count > 0;
}
- (void)showSettings:(id)sender {
    if (!self.settingsWindow) [self buildSettingsInterface];
    if (self.settingsWindow.sheetParent) return;
    self.administratorField.stringValue = self.administratorName ?: @"";
    self.settingsError.stringValue = self.administratorName ? @"" : @"管理者名を設定してください。";
    [self.window beginSheet:self.settingsWindow completionHandler:nil];
}
- (void)closeSettings:(id)sender {
    if (self.settingsWindow.sheetParent) [self.window endSheet:self.settingsWindow];
    [self.settingsWindow orderOut:nil];
}
- (void)saveSettings:(id)sender {
    if ([(NSTextView *)self.administratorField.currentEditor hasMarkedText]) return;
    NSString *name = [self checkedAdministratorName:self.administratorField.stringValue];
    if (!name) { self.settingsError.stringValue = @"管理者名は1〜80文字の一行で入力してください。"; return; }
    if (![name isEqual:self.administratorName]) {
        NSURL *file = self.settingsFile;
        NSError *error = nil;
        NSMutableDictionary *preferences = [[NSDictionary dictionaryWithContentsOfURL:file] mutableCopy] ?: [NSMutableDictionary new];
        preferences[@"administrator_name"] = name;
        NSData *data = [NSPropertyListSerialization dataWithPropertyList:preferences format:NSPropertyListBinaryFormat_v1_0 options:0 error:&error];
        BOOL ready = [NSFileManager.defaultManager createDirectoryAtURL:file.URLByDeletingLastPathComponent
                withIntermediateDirectories:YES attributes:@{NSFilePosixPermissions:@0700} error:&error];
        if (!ready || !data || ![data writeToURL:file options:NSDataWritingAtomic error:&error]) {
            self.settingsError.stringValue = @"設定を保存できませんでした。入力した管理者名は残しています。";
            return;
        }
        // ディスクの置換が成功した値だけを、その後に送るメッセージの送り主へ反映する。
        self.administratorName = name;
    }
    [self closeSettings:nil];
}
- (void)setWorkerControlsEnabled:(BOOL)enabled {
    for (NSButton *button in self.hostButtons) button.enabled = enabled;
    for (NSButton *button in self.managementButtons) button.enabled = enabled;
    self.deleteButton.enabled = enabled && self.checkedDevices.count > 0;
    self.recipientPicker.enabled = enabled && self.registeredDevices.count > 0;
    for (NSButton *check in self.deviceScroll.documentView.subviews) check.enabled = enabled;
}
- (void)applicationDidFinishLaunching:(NSNotification *)note {
    BOOL host = [[NSBundle.mainBundle objectForInfoDictionaryKey:@"DogidoRole"] isEqual:@"host"];
    self.window = [[NSWindow alloc] initWithContentRect:host ? NSMakeRect(0,0,780,590) : NSMakeRect(0,0,660,420)
             styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable
             backing:NSBackingStoreBuffered defer:NO];
    self.window.title = [NSBundle.mainBundle objectForInfoDictionaryKey:@"CFBundleName"];
    self.window.delegate = self;
    if (host) {
        [self buildHostInterface];
    } else {
        NSScrollView *scroll = [[NSScrollView alloc] initWithFrame:NSMakeRect(12,55,636,353)];
        self.output = [[NSTextView alloc] initWithFrame:scroll.contentView.bounds];
        self.output.editable = NO;
        self.output.font = [NSFont monospacedSystemFontOfSize:12 weight:NSFontWeightRegular];
        scroll.documentView = self.output;
        scroll.hasVerticalScroller = YES;
        [self.window.contentView addSubview:scroll];
        self.runButton = [self button:@"停止" action:@selector(toggle:) frame:NSMakeRect(542,10,100,34)];
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
    [self setWorkerControlsEnabled:YES];
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
        NSString *text = line ?: @"[ログの文字コードを読み取れません]\n";
        if (self.hostOutputBuffer) [self.hostOutputBuffer appendString:text];
        else [self append:text];
        [self.pending replaceBytesInRange:NSMakeRange(0,end) withBytes:NULL length:0];
    }
    // pipeの分割位置とUTF-8の境界に依存せず、一行の設定通知だけをUIへ渡す。
    while (self.hostOutputBuffer.length) {
        NSRange newline = [self.hostOutputBuffer rangeOfString:@"\n"];
        if (newline.location == NSNotFound && !final) break;
        NSUInteger count = newline.location == NSNotFound ? self.hostOutputBuffer.length : newline.location + 1;
        NSString *line = [self.hostOutputBuffer substringToIndex:count];
        [self.hostOutputBuffer deleteCharactersInRange:NSMakeRange(0,count)];
        [self receiveHostLine:line];
    }
}
- (void)receiveHostLine:(NSString *)line {
    NSString *prefix = @"\x1e" @"DOGIDO_DEVICES ";
    if (![line hasPrefix:prefix]) { [self append:line]; return; }
    NSData *data = [[line substringFromIndex:prefix.length] dataUsingEncoding:NSUTF8StringEncoding];
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:nil];
    if (![value isKindOfClass:NSDictionary.class] || ![value[@"devices"] isKindOfClass:NSArray.class]) return;
    NSMutableSet *ids = [NSMutableSet new];
    for (id row in value[@"devices"]) {
        if (![row isKindOfClass:NSDictionary.class] || ![row[@"id"] isKindOfClass:NSString.class] ||
                ![row[@"name"] isKindOfClass:NSString.class] || ![row[@"name"] length] ||
                [ids containsObject:row[@"id"]]) return;
        [ids addObject:row[@"id"]];
    }
    id recipient = value[@"recipient"];
    if (recipient != NSNull.null && ![ids containsObject:recipient]) return;
    self.registeredDevices = value[@"devices"];
    self.recipientID = recipient == NSNull.null ? nil : recipient;
    [self refreshDeviceList];
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
    [self setWorkerControlsEnabled:NO];
    if (self.quitting) [NSApp replyToApplicationShouldTerminate:YES];
}
- (BOOL)command:(NSString *)command {
    if (!self.task.running) return NO;
    NSError *error = nil;
    if (![self.input writeData:[command dataUsingEncoding:NSUTF8StringEncoding] error:&error]) {
        [self append:[error.localizedDescription stringByAppendingString:@"\n"]];
        return NO;
    }
    return YES;
}
- (void)pair:(id)sender { [self command:@"pair\n"]; }
- (BOOL)deviceCommand:(NSString *)name value:(id)value {
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingFragmentsAllowed error:nil];
    if (!data) return NO;
    NSString *json = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    return [self command:[NSString stringWithFormat:@"%@ %@\n", name, json]];
}
- (void)revoke:(id)sender {
    if (!self.checkedDevices.count) return;
    if ([self deviceCommand:@"revoke" value:[self.checkedDevices.allObjects sortedArrayUsingSelector:@selector(compare:)]]) {
        [self.checkedDevices removeAllObjects];
        [self refreshDeviceList];
    }
}
- (void)recipient:(id)sender {
    NSString *device = self.recipientPicker.selectedItem.representedObject;
    if (device && ![device isEqual:self.recipientID]) [self deviceCommand:@"recipient" value:device];
    // 書込み成功を保存成功とみなさず、workerの次の通知で選択を確定する。
    [self refreshDeviceList];
}
- (void)sendMessage:(id)sender {
    // ボタン操作中も、日本語などの未確定文字列を送信しない。
    NSTextView *editor = (NSTextView *)self.messageField.currentEditor;
    if (editor.hasMarkedText) return;
    if (!self.administratorName.length) { [self showSettings:nil]; return; }
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
    NSDictionary *message = @{@"text":text, @"sender":self.administratorName};
    NSData *encoded = [NSJSONSerialization dataWithJSONObject:message options:0 error:nil];
    if (!encoded) {
        [self append:@"伝言の文字を読み取れませんでした。入力内容を確認してください。\n"];
        return;
    }
    // 本文に引用符・改行があっても、子プロセスへはコマンド一行だけを渡す。
    NSString *json = [[NSString alloc] initWithData:encoded encoding:NSUTF8StringEncoding];
    // 子プロセスへ渡せた入力だけ消し、停止中や書込み失敗なら再送できるよう残す。
    if ([self command:[NSString stringWithFormat:@"message %@\n", json]]) {
        self.messageField.stringValue = @"";
        [self.window makeFirstResponder:self.messageField];
    }
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
