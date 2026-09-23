// Secrets travel only through anonymous pipes; never through arguments or files.
#import <Foundation/Foundation.h>
#import <Security/Security.h>
#import <LocalAuthentication/LocalAuthentication.h>

static void reply(NSDictionary *object) {
    NSData *data = [NSJSONSerialization dataWithJSONObject:object options:0 error:nil];
    [[NSFileHandle fileHandleWithStandardOutput] writeData:data];
}
int main(void) { @autoreleasepool {
    NSData *input = [[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
    id request = input.length <= 32768 ? [NSJSONSerialization JSONObjectWithData:input options:0 error:nil] : nil;
    if (![request isKindOfClass:NSDictionary.class]) { reply(@{@"ok":@NO}); return 0; }
    NSString *operation = request[@"operation"], *account = request[@"account"];
    if (![operation isKindOfClass:NSString.class] || ![account isKindOfClass:NSString.class] || account.length == 0 || [account lengthOfBytesUsingEncoding:NSUTF8StringEncoding] > 4096) {
        reply(@{@"ok":@NO}); return 0;
    }
    LAContext *context = [[LAContext alloc] init];
    context.interactionNotAllowed = YES;
    NSMutableDictionary *query = [@{
        (__bridge id)kSecClass:(__bridge id)kSecClassGenericPassword,
        (__bridge id)kSecAttrService:@"local.shu.shipment-workbench.api",
        (__bridge id)kSecAttrAccount:account,
        // A locked/denied keychain gives a useful error instead of an unseen prompt.
        (__bridge id)kSecUseAuthenticationContext:context
    } mutableCopy];
    OSStatus status = errSecParam;
    if ([operation isEqualToString:@"load"]) {
        query[(__bridge id)kSecReturnData] = @YES;
        query[(__bridge id)kSecMatchLimit] = (__bridge id)kSecMatchLimitOne;
        CFTypeRef result = NULL;
        status = SecItemCopyMatching((__bridge CFDictionaryRef)query, &result);
        if (status == errSecItemNotFound) { reply(@{@"ok":@YES}); return 0; }
        if (status == errSecSuccess && result) {
            NSData *data = CFBridgingRelease(result);
            NSString *secret = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
            if (secret) { reply(@{@"ok":@YES,@"secret":secret}); return 0; }
        } else if (result) { CFRelease(result); }
    } else if ([operation isEqualToString:@"save"]) {
        NSString *secret = request[@"secret"];
        if (![secret isKindOfClass:NSString.class] || secret.length == 0 || [secret lengthOfBytesUsingEncoding:NSUTF8StringEncoding] > 8192) { reply(@{@"ok":@NO}); return 0; }
        NSData *data = [secret dataUsingEncoding:NSUTF8StringEncoding];
        status = SecItemUpdate((__bridge CFDictionaryRef)query, (__bridge CFDictionaryRef)@{(__bridge id)kSecValueData:data});
        if (status == errSecItemNotFound) {
            query[(__bridge id)kSecValueData] = data;
            query[(__bridge id)kSecAttrLabel] = @"出运工作台 API 密钥";
            status = SecItemAdd((__bridge CFDictionaryRef)query, NULL);
        }
    } else if ([operation isEqualToString:@"delete"]) {
        status = SecItemDelete((__bridge CFDictionaryRef)query);
        if (status == errSecItemNotFound) status = errSecSuccess;
    }
    reply(@{@"ok":@(status == errSecSuccess), @"status":@(status)});
    return 0;
} }
