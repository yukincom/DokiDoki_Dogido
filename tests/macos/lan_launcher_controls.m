// 見込み: 入力経路を既存sendMessageへ集約すると、未確定文字・失敗時の入力保持を共通にできる。
// 合格: 実AppKitの入力欄と設定画面で、下記の送信内容・件数・保持状態が一致する。
// 確認範囲: workerへの書込みだけ模擬化。実キーボード・日本語IME・LAN配送は別途の実画面確認。
#define main DogidoLauncherEntry
#import "../../scripts/lan_apps/launcher.m"
#undef main

@interface ControlTestTask : NSTask
@property BOOL active;
@end
@implementation ControlTestTask
- (BOOL)isRunning { return self.active; }
@end

@interface ControlTestLauncher : Launcher
@property NSMutableArray<NSString *> *commands;
@property BOOL writesSucceed;
@property NSURL *testSettingsFile;
@end
@implementation ControlTestLauncher
- (NSURL *)settingsFile { return self.testSettingsFile; }
- (BOOL)command:(NSString *)command {
    if (!self.task.running || !self.writesSucceed) return NO;
    [self.commands addObject:command];
    return YES;
}
@end

static void require(BOOL condition, const char *reason) {
    if (!condition) { fprintf(stderr, "%s\n", reason); exit(1); }
}
static void submit(ControlTestLauncher *host) {
    [host.messageField sendAction:host.messageField.action to:host.messageField.target];
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        require(argc == 2, "isolated settings path required");
        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyProhibited];
        ControlTestLauncher *host = [ControlTestLauncher new];
        host.testSettingsFile = [NSURL fileURLWithPath:@(argv[1])];
        host.commands = [NSMutableArray new];
        host.writesSucceed = YES;
        ControlTestTask *task = [ControlTestTask new]; task.active = YES; host.task = task;
        host.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,780,590)
                styleMask:NSWindowStyleMaskBorderless backing:NSBackingStoreBuffered defer:NO];
        [host buildHostInterface];
        [host buildSettingsInterface];
        require([host.administratorName isEqual:@"管理者"], "new app assumes family role");

        // 見込み: 保存に成功した名前だけが次の送信へ使われる。合格: 一時plistと再読込が同じ名前。
        host.administratorField.stringValue = @"おとうさん";
        [host saveSettings:nil];
        require([host.administratorName isEqual:@"おとうさん"], "administrator not saved");
        require([[NSDictionary dictionaryWithContentsOfURL:host.settingsFile][@"administrator_name"] isEqual:@"おとうさん"], "administrator not persisted");
        ControlTestLauncher *reloaded = [ControlTestLauncher new];
        reloaded.testSettingsFile = host.testSettingsFile;
        reloaded.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0,0,780,590)
                styleMask:NSWindowStyleMaskBorderless backing:NSBackingStoreBuffered defer:NO];
        [reloaded buildHostInterface];
        require([reloaded.administratorName isEqual:@"おとうさん"], "administrator not reloaded");

        // 見込み: 名前が不正、または保存先が書けなければ前の設定を保つ。合格: canonical値不変、入力は保持。
        host.administratorField.stringValue = @"\n"; [host saveSettings:nil];
        require([host.administratorName isEqual:@"おとうさん"], "invalid name applied");
        NSURL *validFile = host.testSettingsFile;
        host.testSettingsFile = [validFile URLByAppendingPathComponent:@"cannot-write.plist"];
        host.administratorField.stringValue = @"変更後"; [host saveSettings:nil];
        require([host.administratorName isEqual:@"おとうさん"] && [host.administratorField.stringValue isEqual:@"変更後"], "failed settings replaced name or draft");
        host.testSettingsFile = validFile;

        // 見込み: 確定した日本語・引用符・絵文字は一回のコマンドになる。合格: JSON本文が一致し、成功後だけ空欄。
        NSString *message = @"日本語の伝言です。\"おやつ\" 😊";
        host.messageField.stringValue = message; submit(host);
        require(host.commands.count == 1 && host.messageField.stringValue.length == 0, "successful input not cleared once");
        NSString *json = [host.commands.lastObject substringFromIndex:8];
        id decoded = [NSJSONSerialization JSONObjectWithData:[json dataUsingEncoding:NSUTF8StringEncoding]
                options:NSJSONReadingFragmentsAllowed error:nil];
        require([decoded isEqual:@{@"text":message, @"sender":@"おとうさん"}], "message JSON changed");

        // 見込み: 空欄・上限超過・停止・書込み失敗では送信しない。合格: 件数不変で再送可能な原文を保持。
        host.messageField.stringValue = @" \n "; submit(host);
        require(host.commands.count == 1, "empty message sent");
        NSString *longText = [@"" stringByPaddingToLength:501 withString:@"あ" startingAtIndex:0];
        host.messageField.stringValue = longText; submit(host);
        require(host.commands.count == 1 && [host.messageField.stringValue isEqual:longText], "overlong draft lost");
        host.messageField.stringValue = @"残しておく"; task.active = NO; submit(host);
        require(host.commands.count == 1 && [host.messageField.stringValue isEqual:@"残しておく"], "stopped draft lost");
        [host setWorkerControlsEnabled:NO];
        for (NSButton *button in host.managementButtons) require(!button.enabled, "stopped setting enabled");
        task.active = YES; host.writesSucceed = NO; submit(host);
        require(host.commands.count == 1 && [host.messageField.stringValue isEqual:@"残しておく"], "failed draft lost");
        host.writesSucceed = YES;

        // 見込み: 未確定文字列を持つfield editorは送信しない。合格: marked text中のactionでも件数不変。
        [host.window makeFirstResponder:host.messageField];
        NSTextView *editor = (NSTextView *)host.messageField.currentEditor;
        require(editor != nil, "field editor unavailable");
        [editor setMarkedText:@"へんかん" selectedRange:NSMakeRange(4,0) replacementRange:NSMakeRange(NSNotFound,0)];
        require(editor.hasMarkedText, "marked text unavailable");
        submit(host);
        require(host.commands.count == 1, "composition sent prematurely");
        [editor unmarkText];

        // 見込み: PC一覧はworker通知を表示し、選択の保存は既存commandへ届く。合格: 内部IDを表示しない。
        host.messageField.stringValue = @"設定中の下書き";
        host.pending = [NSMutableData new];
        NSString *snapshot = @"\x1e" @"DOGIDO_DEVICES {\"devices\":[{\"id\":\"1111111111111111\",\"name\":\"遊ぶMac\"},{\"id\":\"2222222222222222\",\"name\":\"遊ぶMac\"}],\"recipient\":\"1111111111111111\"}\n";
        NSData *bytes = [snapshot dataUsingEncoding:NSUTF8StringEncoding];
        const unsigned char *raw = bytes.bytes;
        NSUInteger split = 0;
        while (split < bytes.length && raw[split] < 0x80) split++;
        split++;
        [host displayData:[bytes subdataWithRange:NSMakeRange(0,split)] final:NO];
        require(host.registeredDevices.count == 0, "partial snapshot applied");
        [host displayData:[bytes subdataWithRange:NSMakeRange(split,bytes.length-split)] final:NO];
        require(host.registeredDevices.count == 2, "split UTF-8 snapshot missing");
        NSArray<NSButton *> *checks = host.deviceScroll.documentView.subviews;
        require(checks.count == 2 && [checks[0].title isEqual:@"遊ぶMac"], "registered name missing");
        require(![host.output.string containsString:@"1111111111111111"], "internal ID visible in log");
        require([host.recipientPicker.selectedItem.representedObject isEqual:@"1111111111111111"], "saved recipient missing");
        [host setWorkerControlsEnabled:YES];
        require(!host.deleteButton.enabled, "unchecked delete enabled");
        [host.managementButtons[0] performClick:nil];
        require([host.commands.lastObject isEqual:@"pair\n"], "registration did not use pair");

        // 見込み: チェックは削除候補にだけ作用し、宛先とは独立する。合格: 解除時/停止時は削除を押せない。
        [checks[0] performClick:nil];
        require(host.deleteButton.enabled && [host.recipientID isEqual:@"1111111111111111"], "checkbox changed recipient");
        [checks[0] performClick:nil];
        require(!host.deleteButton.enabled, "unchecking did not disable delete");
        [checks[0] performClick:nil];
        [host setWorkerControlsEnabled:NO];
        require(!host.deleteButton.enabled && !host.recipientPicker.enabled, "stopped controls enabled");
        [host setWorkerControlsEnabled:YES];
        require(host.deleteButton.enabled, "checked delete did not resume");

        // 見込み: 同名でもmenu itemのIDを保存する。合格: 保存通知前は元の宛先を表示し、通知後に新宛先。
        [host.recipientPicker selectItemAtIndex:2];
        [host recipient:host.recipientPicker];
        require([host.commands.lastObject isEqual:@"recipient \"2222222222222222\"\n"], "same-name PC mixed up");
        require([host.recipientPicker.selectedItem.representedObject isEqual:@"1111111111111111"], "unconfirmed recipient applied");
        NSString *changed = [snapshot stringByReplacingOccurrencesOfString:@"\"recipient\":\"1111111111111111\"" withString:@"\"recipient\":\"2222222222222222\""];
        [host displayData:[changed dataUsingEncoding:NSUTF8StringEncoding] final:NO];
        require([host.recipientPicker.selectedItem.representedObject isEqual:@"2222222222222222"], "saved recipient not reflected");
        [host.deleteButton performClick:nil];
        require([host.commands.lastObject isEqual:@"revoke [\"1111111111111111\"]\n"], "delete targeted unchecked device");
        require(!host.deleteButton.enabled && host.checkedDevices.count == 0, "submitted checks not cleared");
        require([host.messageField.stringValue isEqual:@"設定中の下書き"] && host.commands.count == 4, "settings changed message draft");
        host.window.delegate = nil;
        [host.window orderOut:nil];
        puts("host controls passed");
    }
    return 0;
}
