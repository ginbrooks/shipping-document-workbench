#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>
#import <sys/socket.h>
#import <netinet/in.h>
#import <arpa/inet.h>
#import <signal.h>
#import <unistd.h>
@interface Desk : NSObject <NSApplicationDelegate,NSWindowDelegate,WKNavigationDelegate,WKUIDelegate,WKDownloadDelegate>
@property NSWindow *window;
@property WKWebView *web;
@property NSView *overlay;
@property NSTextField *status;
@property NSTask *server;
@property NSTimer *timer;
@property NSURL *root,*origin;
@property NSFileHandle *log;
@property NSDate *started;
@property BOOL ready,failed,quitting;
@property NSMutableArray *previews;
@end
@implementation Desk
- (void)applicationDidFinishLaunching:(NSNotification *)n {
 [NSApp setActivationPolicy:NSApplicationActivationPolicyRegular];
 self.root=NSBundle.mainBundle.bundleURL.URLByDeletingLastPathComponent;self.previews=[NSMutableArray new];
 NSMenu *bar=[NSMenu new],*app=[NSMenu new],*edit=[[NSMenu alloc]initWithTitle:@"编辑"],*win=[[NSMenu alloc]initWithTitle:@"窗口"];
 for(NSMenu *menu in @[app,edit,win]){NSMenuItem *item=[NSMenuItem new];item.title=menu.title;item.submenu=menu;[bar addItem:item];}
 [[app addItemWithTitle:@"关于出运工作台" action:@selector(about:) keyEquivalent:@""]setTarget:self];
 [app addItem:NSMenuItem.separatorItem];[app addItemWithTitle:@"隐藏出运工作台" action:@selector(hide:) keyEquivalent:@"h"];
 [app addItemWithTitle:@"退出出运工作台" action:@selector(terminate:) keyEquivalent:@"q"];
 NSArray *titles=@[@"撤销",@"剪切",@"复制",@"粘贴",@"全选"],*selectors=@[@"undo:",@"cut:",@"copy:",@"paste:",@"selectAll:"],*keys=@[@"z",@"x",@"c",@"v",@"a"];
 for(int i=0;i<5;i++)[edit addItemWithTitle:titles[i] action:NSSelectorFromString(selectors[i]) keyEquivalent:keys[i]];
 [win addItemWithTitle:@"最小化" action:@selector(performMiniaturize:) keyEquivalent:@"m"];
 [[win addItemWithTitle:@"重新载入" action:@selector(reload:) keyEquivalent:@"r"]setTarget:self];
 [[win addItemWithTitle:@"打开本地文件夹" action:@selector(openData:) keyEquivalent:@""]setTarget:self];NSApp.mainMenu=bar;NSApp.windowsMenu=win;
 self.window=[[NSWindow alloc]initWithContentRect:NSMakeRect(0,0,1360,900) styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskMiniaturizable|NSWindowStyleMaskResizable backing:NSBackingStoreBuffered defer:NO];
 self.window.title=@"出运工作台";self.window.subtitle=@"本地工作空间";self.window.minSize=NSMakeSize(1050,740);self.window.delegate=self;self.window.releasedWhenClosed=NO;
 [self.window setFrameAutosaveName:@"ShipmentWorkbenchWindow"];[self.window center];
 self.web=[[WKWebView alloc]initWithFrame:self.window.contentView.bounds];self.web.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;self.web.navigationDelegate=self;self.web.UIDelegate=self;[self.window.contentView addSubview:self.web];
 self.overlay=[[NSView alloc]initWithFrame:self.window.contentView.bounds];self.overlay.autoresizingMask=NSViewWidthSizable|NSViewHeightSizable;self.overlay.wantsLayer=YES;self.overlay.layer.backgroundColor=[NSColor colorWithCalibratedRed:.96 green:.965 blue:.95 alpha:1].CGColor;
 NSTextField *title=[NSTextField labelWithString:@"出运工作台"];title.font=[NSFont systemFontOfSize:28 weight:NSFontWeightSemibold];self.status=[NSTextField labelWithString:@"正在打开本地工作空间…"];
 NSProgressIndicator *spin=[NSProgressIndicator new];spin.style=NSProgressIndicatorStyleSpinning;[spin startAnimation:nil];NSStackView *stack=[NSStackView stackViewWithViews:@[title,self.status,spin]];stack.orientation=NSUserInterfaceLayoutOrientationVertical;stack.spacing=20;stack.translatesAutoresizingMaskIntoConstraints=NO;[self.overlay addSubview:stack];[NSLayoutConstraint activateConstraints:@[[stack.centerXAnchor constraintEqualToAnchor:self.overlay.centerXAnchor],[stack.centerYAnchor constraintEqualToAnchor:self.overlay.centerYAnchor]]];[self.window.contentView addSubview:self.overlay];
 [self.window makeKeyAndOrderFront:nil];[NSApp activateIgnoringOtherApps:YES];
 if(![NSFileManager.defaultManager fileExistsAtPath:[self.root URLByAppendingPathComponent:@"app.py"].path]){[self error:@"找不到工作台程序" detail:@"请把应用保留在 shipping_workbench 文件夹中，与 app.py 放在一起。"];return;}[self start];
}
- (void)error:(NSString *)title detail:(NSString *)detail {
 if(self.failed||self.quitting)return;self.failed=YES;[self.timer invalidate];self.status.stringValue=title;NSAlert *alert=[NSAlert new];alert.messageText=title;alert.informativeText=detail;[alert addButtonWithTitle:@"关闭应用"];[alert beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse r){[NSApp terminate:nil];}];
}
- (void)start {
 int sock=socket(AF_INET,SOCK_STREAM,0);struct sockaddr_in a={0};a.sin_len=sizeof(a);a.sin_family=AF_INET;a.sin_addr.s_addr=inet_addr("127.0.0.1");socklen_t length=sizeof(a);
 if(sock<0||bind(sock,(struct sockaddr *)&a,sizeof(a))!=0||getsockname(sock,(struct sockaddr *)&a,&length)!=0){if(sock>=0)close(sock);[self error:@"无法启动本地服务" detail:@"无法分配本机端口，请重新打开应用。"];return;}int port=ntohs(a.sin_port);close(sock);
 self.origin=[NSURL URLWithString:[NSString stringWithFormat:@"http://127.0.0.1:%d",port]];NSURL *data=[self.root URLByAppendingPathComponent:@"data"],*logURL=[data URLByAppendingPathComponent:@"desktop.log"];NSError *err=nil;
 [NSFileManager.defaultManager createDirectoryAtURL:data withIntermediateDirectories:YES attributes:nil error:&err];
 if(![NSFileManager.defaultManager fileExistsAtPath:logURL.path])[NSFileManager.defaultManager createFileAtPath:logURL.path contents:nil attributes:nil];self.log=[NSFileHandle fileHandleForWritingToURL:logURL error:&err];
 if(!self.log){[self error:@"本地文件夹不可写" detail:err.localizedDescription];return;}[self.log seekToEndOfFile];
 self.server=[NSTask new];self.server.currentDirectoryURL=self.root;NSURL *python=[self.root URLByAppendingPathComponent:@".venv/bin/python"];NSString *launcher=[self.root URLByAppendingPathComponent:@"desktop/server.py"].path;
 if([NSFileManager.defaultManager isExecutableFileAtPath:python.path]){self.server.executableURL=python;self.server.arguments=@[launcher,@(port).stringValue];}else{self.server.executableURL=[NSURL fileURLWithPath:@"/usr/bin/env"];self.server.arguments=@[@"python3.11",launcher,@(port).stringValue];}
 NSMutableDictionary *env=[NSProcessInfo.processInfo.environment mutableCopy];env[@"PATH"]=[@"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" stringByAppendingString:env[@"PATH"]?:@""];env[@"SHIPPING_DATA_DIR"]=data.path;env[@"SHIPPING_DESKTOP"]=@"1";self.server.environment=env;self.server.standardOutput=self.log;self.server.standardError=self.log;
 __weak Desk *weak=self;self.server.terminationHandler=^(NSTask *task){dispatch_async(dispatch_get_main_queue(),^{[weak error:@"本地服务已停止" detail:@"请重新打开应用。首次运行需要 Python 3.11；具体原因可查看 data/desktop.log。"];});};
 if(![self.server launchAndReturnError:&err]){[self error:@"无法启动工作台" detail:err.localizedDescription];return;}
 NSData *session=[NSJSONSerialization dataWithJSONObject:@{@"pid":@(self.server.processIdentifier),@"port":@(port)} options:0 error:nil];[session writeToURL:[data URLByAppendingPathComponent:@"desktop-session.json"] atomically:YES];self.started=NSDate.date;
 self.timer=[NSTimer scheduledTimerWithTimeInterval:.4 repeats:YES block:^(NSTimer *t){[weak health];}];
}
- (void)health {
 if(self.ready||self.failed)return;if(-self.started.timeIntervalSinceNow>600){[self error:@"启动超时" detail:@"请检查 Python 3.11 与依赖安装情况，日志在 data/desktop.log。"];return;}if(-self.started.timeIntervalSinceNow>8)self.status.stringValue=@"首次准备依赖可能需要几分钟，请稍候…";
 NSMutableURLRequest *req=[NSMutableURLRequest requestWithURL:[self.origin URLByAppendingPathComponent:@"_stcore/health"]];req.timeoutInterval=1;
 [[NSURLSession.sharedSession dataTaskWithRequest:req completionHandler:^(NSData *data,NSURLResponse *response,NSError *error){if(![[[NSString alloc]initWithData:data?:NSData.data encoding:NSUTF8StringEncoding] isEqualToString:@"ok"])return;dispatch_async(dispatch_get_main_queue(),^{if(self.ready||self.failed||self.quitting)return;self.ready=YES;[self.timer invalidate];[self.web loadRequest:[NSURLRequest requestWithURL:self.origin]];});}]resume];
}
- (void)webView:(WKWebView *)web didFinishNavigation:(WKNavigation *)n {if(web==self.web)[self.overlay removeFromSuperview];}
- (void)webView:(WKWebView *)web didFailProvisionalNavigation:(WKNavigation *)n withError:(NSError *)error {if(error.code!=NSURLErrorCancelled)[self error:@"页面无法打开" detail:error.localizedDescription];}
- (void)webView:(WKWebView *)web decidePolicyForNavigationAction:(WKNavigationAction *)action decisionHandler:(void (^)(WKNavigationActionPolicy))done {
 NSURL *u=action.request.URL;if(action.shouldPerformDownload){done(WKNavigationActionPolicyDownload);return;}
 if(action.targetFrame.mainFrame&&([u.scheme isEqual:@"https"]||[u.scheme isEqual:@"http"])&&(![u.host isEqual:self.origin.host]||![u.port isEqual:self.origin.port])){if(action.navigationType==WKNavigationTypeLinkActivated)[NSWorkspace.sharedWorkspace openURL:u];done(WKNavigationActionPolicyCancel);return;}done(WKNavigationActionPolicyAllow);
}
- (void)webView:(WKWebView *)web decidePolicyForNavigationResponse:(WKNavigationResponse *)response decisionHandler:(void (^)(WKNavigationResponsePolicy))done {done(response.canShowMIMEType?WKNavigationResponsePolicyAllow:WKNavigationResponsePolicyDownload);}
- (void)webView:(WKWebView *)web navigationAction:(WKNavigationAction *)action didBecomeDownload:(WKDownload *)download {download.delegate=self;}
- (void)webView:(WKWebView *)web navigationResponse:(WKNavigationResponse *)response didBecomeDownload:(WKDownload *)download {download.delegate=self;}
- (void)download:(WKDownload *)download decideDestinationUsingResponse:(NSURLResponse *)response suggestedFilename:(NSString *)name completionHandler:(void (^)(NSURL *))done {
 NSSavePanel *panel=[NSSavePanel savePanel];panel.title=@"保存工作台文件";panel.nameFieldStringValue=name.lastPathComponent;panel.canCreateDirectories=YES;
 [panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse r){done(r==NSModalResponseOK?panel.URL:nil);}];
}
- (void)downloadDidFinish:(WKDownload *)download {self.window.subtitle=@"文件已保存";dispatch_after(dispatch_time(DISPATCH_TIME_NOW,5*NSEC_PER_SEC),dispatch_get_main_queue(),^{self.window.subtitle=@"本地工作空间";});}
- (void)download:(WKDownload *)download didFailWithError:(NSError *)error resumeData:(NSData *)data {if(error.code==NSURLErrorCancelled)return;NSAlert *alert=[NSAlert new];alert.messageText=@"文件未保存";alert.informativeText=error.localizedDescription;[alert beginSheetModalForWindow:self.window completionHandler:nil];}
- (void)webView:(WKWebView *)web runOpenPanelWithParameters:(WKOpenPanelParameters *)params initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(NSArray<NSURL *> *))done {
 NSOpenPanel *panel=[NSOpenPanel openPanel];panel.title=@"选择出运资料";panel.canChooseDirectories=NO;panel.canChooseFiles=YES;panel.allowsMultipleSelection=params.allowsMultipleSelection;[panel beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse r){done(r==NSModalResponseOK?panel.URLs:nil);}];
}
- (WKWebView *)webView:(WKWebView *)web createWebViewWithConfiguration:(WKWebViewConfiguration *)config forNavigationAction:(WKNavigationAction *)action windowFeatures:(WKWindowFeatures *)features {
 if(action.targetFrame)return nil;WKWebView *child=[[WKWebView alloc]initWithFrame:NSMakeRect(0,0,1000,760) configuration:config];child.navigationDelegate=self;child.UIDelegate=self;
 NSWindow *preview=[[NSWindow alloc]initWithContentRect:child.bounds styleMask:NSWindowStyleMaskTitled|NSWindowStyleMaskClosable|NSWindowStyleMaskResizable backing:NSBackingStoreBuffered defer:NO];preview.title=@"文件预览";preview.contentView=child;preview.releasedWhenClosed=NO;[preview center];[preview makeKeyAndOrderFront:nil];[self.previews addObject:preview];return child;
}
- (void)webView:(WKWebView *)web runJavaScriptAlertPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(void))done {NSAlert *a=[NSAlert new];a.messageText=message;[a beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse r){done();}];}
- (void)webView:(WKWebView *)web runJavaScriptConfirmPanelWithMessage:(NSString *)message initiatedByFrame:(WKFrameInfo *)frame completionHandler:(void (^)(BOOL))done {NSAlert *a=[NSAlert new];a.messageText=message;[a addButtonWithTitle:@"继续"];[a addButtonWithTitle:@"取消"];[a beginSheetModalForWindow:self.window completionHandler:^(NSModalResponse r){done(r==NSAlertFirstButtonReturn);}];}
- (void)applicationWillTerminate:(NSNotification *)n {self.quitting=YES;[self.timer invalidate];if(self.server.running){pid_t p=self.server.processIdentifier;kill(-p,SIGTERM);kill(p,SIGTERM);}[self.log closeFile];[NSFileManager.defaultManager removeItemAtURL:[self.root URLByAppendingPathComponent:@"data/desktop-session.json"] error:nil];}
- (void)windowWillClose:(NSNotification *)n {if(n.object==self.window)[NSApp terminate:nil];}
- (BOOL)applicationShouldHandleReopen:(NSApplication *)app hasVisibleWindows:(BOOL)visible {[self.window makeKeyAndOrderFront:nil];return YES;}
- (void)reload:(id)sender {[self.web reload];}
- (void)openData:(id)sender {[NSWorkspace.sharedWorkspace openURL:[self.root URLByAppendingPathComponent:@"data"]];}
- (void)about:(id)sender {[NSApp orderFrontStandardAboutPanelWithOptions:@{NSAboutPanelOptionApplicationName:@"出运工作台",NSAboutPanelOptionApplicationVersion:@"3.0 按文件办理"}];}
@end
int main(int argc,const char *argv[]){@autoreleasepool{NSApplication *app=NSApplication.sharedApplication;Desk *delegate=[Desk new];app.delegate=delegate;[app run];}return 0;}
