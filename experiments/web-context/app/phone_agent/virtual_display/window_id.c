#include <ApplicationServices/ApplicationServices.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    if (argc != 3) {
        return 64;
    }

    int wanted_pid = atoi(argv[2]);

    CFStringRef wanted = CFStringCreateWithCString(
        kCFAllocatorDefault, argv[1], kCFStringEncodingUTF8);
    if (wanted == NULL) {
        return 65;
    }

    CGWindowListOption options =
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements;
    CFArrayRef windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID);
    if (windows == NULL) {
        CFRelease(wanted);
        return 2;
    }

    CFIndex count = CFArrayGetCount(windows);
    int best_window_id = 0;
    double best_area = 0;
    for (CFIndex index = 0; index < count; index++) {
        CFDictionaryRef window = CFArrayGetValueAtIndex(windows, index);
        CFStringRef name = CFDictionaryGetValue(window, kCGWindowName);
        CFNumberRef number = CFDictionaryGetValue(window, kCGWindowNumber);
        CFNumberRef owner_pid_number =
            CFDictionaryGetValue(window, kCGWindowOwnerPID);
        CFNumberRef layer_number = CFDictionaryGetValue(window, kCGWindowLayer);
        CFDictionaryRef bounds = CFDictionaryGetValue(window, kCGWindowBounds);

        int owner_pid = 0;
        int layer = -1;
        if (owner_pid_number != NULL) {
            CFNumberGetValue(owner_pid_number, kCFNumberIntType, &owner_pid);
        }
        if (layer_number != NULL) {
            CFNumberGetValue(layer_number, kCFNumberIntType, &layer);
        }

        Boolean matches = wanted_pid > 0
            ? owner_pid == wanted_pid
            : name != NULL && CFStringCompare(name, wanted, 0) == kCFCompareEqualTo;
        if (!matches || layer != 0 || number == NULL) {
            continue;
        }

        int window_id = 0;
        CGRect rect = CGRectZero;
        if (CFNumberGetValue(number, kCFNumberIntType, &window_id) &&
            bounds != NULL &&
            CGRectMakeWithDictionaryRepresentation(bounds, &rect)) {
            double area = rect.size.width * rect.size.height;
            if (area > best_area) {
                best_area = area;
                best_window_id = window_id;
            }
        }
    }

    CFRelease(windows);
    CFRelease(wanted);
    if (best_window_id > 0) {
        printf("%d\n", best_window_id);
        return 0;
    }
    return 2;
}
