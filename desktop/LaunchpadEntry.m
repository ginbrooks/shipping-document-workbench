// A small Applications entry; the signed workbench and its data stay together.
#import <Cocoa/Cocoa.h>

@interface LaunchpadEntry : NSObject <NSApplicationDelegate>
@end

@implementation LaunchpadEntry
- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    NSString *path = [NSBundle.mainBundle objectForInfoDictionaryKey:@"ShipmentWorkbenchTarget"];
    if (![path isKindOfClass:NSString.class] || !path.isAbsolutePath) {
        [self showError:@"启动入口没有配置工作台位置，请从原项目目录打开工作台。"];
        return;
    }
    NSWorkspaceOpenConfiguration *configuration = [NSWorkspaceOpenConfiguration configuration];
    configuration.activates = YES;
    configuration.createsNewApplicationInstance = NO;
    [NSWorkspace.sharedWorkspace openApplicationAtURL:[NSURL fileURLWithPath:path]
                                       configuration:configuration
                                   completionHandler:^(NSRunningApplication *app, NSError *error) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (error) {
                [self showError:@"未能打开出运工作台。请确认原来的出运项目文件夹仍在原位置，再重新安装启动入口。"];
            } else {
                [NSApp terminate:nil];
            }
        });
    }];
}
- (void)showError:(NSString *)message {
    [NSApp activateIgnoringOtherApps:YES];
    NSAlert *alert = [NSAlert new];
    alert.messageText = @"出运工作台";
    alert.informativeText = message;
    [alert addButtonWithTitle:@"好"];
    [alert runModal];
    [NSApp terminate:nil];
}
@end

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSApplication *app = NSApplication.sharedApplication;
        LaunchpadEntry *entry = [LaunchpadEntry new];
        app.delegate = entry;
        [app run];
    }
    return 0;
}
