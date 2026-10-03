/*
 * Candidate owner-controlled macOS Virtualization.framework slot launcher.
 *
 * This file deliberately has a small, explicit surface.  It accepts only a
 * Linux kernel, an initrd, bounded CPU/memory/time budgets, and (optionally)
 * one fixed guest vsock control port paired with one owner-local UNIX socket.
 * The VM has no network, directory sharing, USB, or storage device.  Its
 * only socket device is one VZVirtioSocketDeviceConfiguration.
 *
 * The JSON emitted on stdout is an external native config/lifecycle receipt.
 * It never includes input paths, serial-console bytes, guest text, or model
 * content.  The launcher is a candidate implementation and does not confer
 * formal admission or Host authority.
 */

#import <Foundation/Foundation.h>
#import <Virtualization/Virtualization.h>

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/un.h>
#include <unistd.h>

static NSString *const kReceiptSchema = @"deeplaw.macos_virtual_slot/v1";
static const NSUInteger kMinCPUCount = 1;
static const NSUInteger kMaxCPUCount = 2;
static const uint64_t kMinMemoryMiB = 512;
static const uint64_t kMaxMemoryMiB = 4096;
static const uint64_t kMaxTimeoutSeconds = 1800;
static const uint32_t kMinVsockPort = 1;
static const uint32_t kMaxVsockPort = 65535;
enum {
  kHandoffFrameBytes = 16,
};
static const uint8_t kHandoffFrameVersion = 1;
static const uint8_t kHandoffFrameType = 1;

enum {
  kExitUsage = 64,
  kExitConfiguration = 65,
  kExitStart = 66,
  kExitRuntime = 67,
  kExitTimeout = 124,
};

typedef NS_ENUM(NSInteger, ParseResult) {
  ParseResultSuccess = 0,
  ParseResultInvalid = -1,
  ParseResultHelp = 1,
};

typedef NS_ENUM(NSInteger, StopReason) {
  StopReasonNone = 0,
  StopReasonSignal,
  StopReasonTimeout,
  StopReasonFailure,
};

@interface SlotOptions : NSObject
@property(nonatomic, copy) NSString *kernelPath;
@property(nonatomic, copy) NSString *initrdPath;
@property(nonatomic, assign) NSUInteger cpuCount;
@property(nonatomic, assign) uint64_t memoryMiB;
@property(nonatomic, assign) uint64_t timeoutSeconds;
@property(nonatomic, assign) BOOL validationOnly;
@property(nonatomic, assign) BOOL hasGuestControlPort;
@property(nonatomic, assign) uint32_t guestControlPort;
@property(nonatomic, copy) NSString *handoffSocketPath;
@end

@implementation SlotOptions
@end

static void emitReceipt(NSDictionary *fields) {
  NSMutableDictionary *receipt = [@{
    @"schema": kReceiptSchema,
    @"formal_admission": @NO,
  } mutableCopy];
  [receipt addEntriesFromDictionary:fields];

  NSData *data = [NSJSONSerialization dataWithJSONObject:receipt options:NSJSONWritingSortedKeys error:nil];
  if (data == nil) {
    fputs("receipt_serialization_failed\n", stderr);
    return;
  }
  fwrite(data.bytes, 1, data.length, stdout);
  fputc('\n', stdout);
  fflush(stdout);
}

static int emitConfigurationFailure(NSString *reason, int exitCode) {
  emitReceipt(@{
    @"kind": @"native_config",
    @"event": @"configuration_rejected",
    @"status": @"failed",
    @"reason": reason,
  });
  return exitCode;
}

static BOOL parseUnsignedDecimal(NSString *text, uint64_t *value) {
  if (text == nil || text.length == 0) {
    return NO;
  }

  const char *raw = text.UTF8String;
  if (raw == NULL || *raw == '\0') {
    return NO;
  }
  for (const unsigned char *cursor = (const unsigned char *)raw; *cursor != '\0'; cursor++) {
    if (*cursor < '0' || *cursor > '9') {
      return NO;
    }
  }

  errno = 0;
  char *end = NULL;
  unsigned long long parsed = strtoull(raw, &end, 10);
  if (errno == ERANGE || end == raw || *end != '\0') {
    return NO;
  }
  *value = (uint64_t)parsed;
  return YES;
}

static BOOL absolutePathText(NSString *path, size_t maximumBytes) {
  if (path == nil || path.length == 0 || ![path hasPrefix:@"/"]) {
    return NO;
  }
  const char *raw = path.fileSystemRepresentation;
  if (raw == NULL || strlen(raw) >= maximumBytes) {
    return NO;
  }
  return YES;
}

static BOOL readableRegularFile(NSString *path) {
  if (!absolutePathText(path, PATH_MAX)) {
    return NO;
  }

  struct stat metadata;
  if (lstat(path.fileSystemRepresentation, &metadata) != 0) {
    return NO;
  }
  if (!S_ISREG(metadata.st_mode) || access(path.fileSystemRepresentation, R_OK) != 0) {
    return NO;
  }
  return YES;
}

static BOOL ownerOnlyDirectory(NSString *path) {
  char resolvedParent[PATH_MAX];
  if (realpath(path.fileSystemRepresentation, resolvedParent) == NULL) {
    return NO;
  }

  struct stat metadata;
  if (lstat(resolvedParent, &metadata) != 0 || !S_ISDIR(metadata.st_mode)) {
    return NO;
  }
  if (metadata.st_uid != geteuid() || (metadata.st_mode & 0022) != 0) {
    return NO;
  }
  return YES;
}

static BOOL validOwnerLocalSocketPath(NSString *path, BOOL requireAbsent) {
  if (!absolutePathText(path, sizeof(((struct sockaddr_un *)0)->sun_path))) {
    return NO;
  }

  NSString *parent = path.stringByDeletingLastPathComponent;
  if (!ownerOnlyDirectory(parent)) {
    return NO;
  }

  struct stat existing;
  if (lstat(path.fileSystemRepresentation, &existing) == 0) {
    return !requireAbsent && S_ISSOCK(existing.st_mode);
  }
  return errno == ENOENT;
}

static void printUsage(void) {
  puts("usage: macos_virtual_slot --kernel PATH --initrd PATH --cpu 1|2 --memory-mib 512..4096 --timeout-seconds 1..1800 [--guest-control-port PORT --fd-handoff-socket PATH] [--validation-only]");
}

static ParseResult parseOptions(int argc, const char *argv[], SlotOptions **result, NSString **failureReason) {
  SlotOptions *options = [SlotOptions new];
  BOOL seenKernel = NO;
  BOOL seenInitrd = NO;
  BOOL seenCPU = NO;
  BOOL seenMemory = NO;
  BOOL seenTimeout = NO;
  BOOL seenValidationOnly = NO;
  BOOL seenPort = NO;
  BOOL seenHandoff = NO;

  if (argc == 2 && strcmp(argv[1], "--help") == 0) {
    return ParseResultHelp;
  }

  for (int index = 1; index < argc; index++) {
    const char *argument = argv[index];
    if (strcmp(argument, "--validation-only") == 0) {
      if (seenValidationOnly) {
        *failureReason = @"duplicate_argument";
        return ParseResultInvalid;
      }
      seenValidationOnly = YES;
      options.validationOnly = YES;
      continue;
    }

    if (strcmp(argument, "--kernel") == 0 || strcmp(argument, "--initrd") == 0 || strcmp(argument, "--cpu") == 0 ||
        strcmp(argument, "--memory-mib") == 0 || strcmp(argument, "--timeout-seconds") == 0 ||
        strcmp(argument, "--guest-control-port") == 0 || strcmp(argument, "--fd-handoff-socket") == 0) {
      if (index + 1 >= argc || argv[index + 1][0] == '\0' || argv[index + 1][0] == '-') {
        *failureReason = @"missing_argument_value";
        return ParseResultInvalid;
      }

      NSString *value = [NSString stringWithUTF8String:argv[++index]];
      if (value == nil) {
        *failureReason = @"invalid_argument_value";
        return ParseResultInvalid;
      }

      if (strcmp(argument, "--kernel") == 0) {
        if (seenKernel) {
          *failureReason = @"duplicate_argument";
          return ParseResultInvalid;
        }
        seenKernel = YES;
        options.kernelPath = value;
      } else if (strcmp(argument, "--initrd") == 0) {
        if (seenInitrd) {
          *failureReason = @"duplicate_argument";
          return ParseResultInvalid;
        }
        seenInitrd = YES;
        options.initrdPath = value;
      } else if (strcmp(argument, "--cpu") == 0) {
        uint64_t parsed = 0;
        if (seenCPU || !parseUnsignedDecimal(value, &parsed) || parsed < kMinCPUCount || parsed > kMaxCPUCount) {
          *failureReason = @"cpu_out_of_bounds";
          return ParseResultInvalid;
        }
        seenCPU = YES;
        options.cpuCount = (NSUInteger)parsed;
      } else if (strcmp(argument, "--memory-mib") == 0) {
        uint64_t parsed = 0;
        if (seenMemory || !parseUnsignedDecimal(value, &parsed) || parsed < kMinMemoryMiB || parsed > kMaxMemoryMiB) {
          *failureReason = @"memory_out_of_bounds";
          return ParseResultInvalid;
        }
        seenMemory = YES;
        options.memoryMiB = parsed;
      } else if (strcmp(argument, "--timeout-seconds") == 0) {
        uint64_t parsed = 0;
        if (seenTimeout || !parseUnsignedDecimal(value, &parsed) || parsed == 0 || parsed > kMaxTimeoutSeconds) {
          *failureReason = @"timeout_out_of_bounds";
          return ParseResultInvalid;
        }
        seenTimeout = YES;
        options.timeoutSeconds = parsed;
      } else if (strcmp(argument, "--guest-control-port") == 0) {
        uint64_t parsed = 0;
        if (seenPort || !parseUnsignedDecimal(value, &parsed) || parsed < kMinVsockPort || parsed > kMaxVsockPort) {
          *failureReason = @"guest_control_port_out_of_bounds";
          return ParseResultInvalid;
        }
        seenPort = YES;
        options.hasGuestControlPort = YES;
        options.guestControlPort = (uint32_t)parsed;
      } else {
        if (seenHandoff) {
          *failureReason = @"duplicate_argument";
          return ParseResultInvalid;
        }
        seenHandoff = YES;
        options.handoffSocketPath = value;
      }
      continue;
    }

    *failureReason = @"unknown_argument";
    return ParseResultInvalid;
  }

  if (!seenKernel || !seenInitrd || !seenCPU || !seenMemory || !seenTimeout) {
    *failureReason = @"missing_required_argument";
    return ParseResultInvalid;
  }
  if (seenPort != seenHandoff) {
    *failureReason = @"handoff_arguments_must_be_paired";
    return ParseResultInvalid;
  }
  if (!readableRegularFile(options.kernelPath) || !readableRegularFile(options.initrdPath)) {
    *failureReason = @"kernel_or_initrd_unavailable";
    return ParseResultInvalid;
  }
  if (seenHandoff && !validOwnerLocalSocketPath(options.handoffSocketPath, YES)) {
    *failureReason = @"handoff_socket_path_invalid";
    return ParseResultInvalid;
  }

  *result = options;
  return ParseResultSuccess;
}

static VZVirtualMachineConfiguration *makeConfiguration(SlotOptions *options) {
  VZVirtualMachineConfiguration *configuration = [VZVirtualMachineConfiguration new];
  VZLinuxBootLoader *bootLoader = [[VZLinuxBootLoader alloc] initWithKernelURL:[NSURL fileURLWithPath:options.kernelPath]];
  bootLoader.initialRamdiskURL = [NSURL fileURLWithPath:options.initrdPath];
  bootLoader.commandLine = @"console=hvc0 rdinit=/init panic=-1";

  configuration.bootLoader = bootLoader;
  configuration.CPUCount = options.cpuCount;
  configuration.memorySize = options.memoryMiB * 1024ULL * 1024ULL;
  configuration.networkDevices = @[];
  configuration.directorySharingDevices = @[];
  configuration.storageDevices = @[];
  configuration.socketDevices = @[[[VZVirtioSocketDeviceConfiguration alloc] init]];
  configuration.serialPorts = @[];

  if (@available(macOS 12.0, *)) {
    configuration.audioDevices = @[];
  }
  if (@available(macOS 13.0, *)) {
    configuration.consoleDevices = @[];
  }
  if (@available(macOS 15.0, *)) {
    configuration.usbControllers = @[];
  }
  return configuration;
}

static void emitConfigurationReceipt(SlotOptions *options, VZVirtualMachineConfiguration *configuration, BOOL validationOnly) {
  NSMutableDictionary *fields = [@{
    @"kind": @"native_config",
    @"event": @"configuration_validated",
    @"status": @"validated",
    @"validation_only": @(validationOnly),
    @"cpu_count": @(options.cpuCount),
    @"memory_mib": @(options.memoryMiB),
    @"timeout_seconds": @(options.timeoutSeconds),
    @"network_devices": @(configuration.networkDevices.count),
    @"directory_sharing_devices": @(configuration.directorySharingDevices.count),
    @"storage_devices": @(configuration.storageDevices.count),
    @"vsock_devices": @(configuration.socketDevices.count),
    @"fd_handoff_enabled": @(options.hasGuestControlPort),
  } mutableCopy];
  if (@available(macOS 15.0, *)) {
    fields[@"usb_controllers"] = @(configuration.usbControllers.count);
  } else {
    fields[@"usb_controllers"] = @0;
  }
  if (options.hasGuestControlPort) {
    fields[@"guest_control_port"] = @(options.guestControlPort);
  }
  emitReceipt(fields);
}

static BOOL setNonBlocking(int descriptor) {
  int flags = fcntl(descriptor, F_GETFL, 0);
  if (flags < 0 || fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) < 0) {
    return NO;
  }
  return YES;
}

static BOOL ownerPeerMatches(int descriptor) {
  uid_t peerUID = (uid_t)-1;
  gid_t peerGID = (gid_t)-1;
  if (getpeereid(descriptor, &peerUID, &peerGID) != 0) {
    return NO;
  }
  return peerUID == geteuid();
}

static int createHandoffListener(NSString *path, struct stat *boundMetadata) {
  int descriptor = socket(AF_UNIX, SOCK_STREAM, 0);
  if (descriptor < 0) {
    return -1;
  }

  if (!setNonBlocking(descriptor)) {
    close(descriptor);
    return -1;
  }

  int noSignal = 1;
  (void)setsockopt(descriptor, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, sizeof(noSignal));

  struct sockaddr_un address;
  memset(&address, 0, sizeof(address));
  address.sun_family = AF_UNIX;
  const char *rawPath = path.fileSystemRepresentation;
  size_t pathLength = strlen(rawPath);
  if (pathLength == 0 || pathLength >= sizeof(address.sun_path)) {
    close(descriptor);
    return -1;
  }
  memcpy(address.sun_path, rawPath, pathLength + 1);
  socklen_t addressLength = (socklen_t)(offsetof(struct sockaddr_un, sun_path) + pathLength + 1);

  if (bind(descriptor, (const struct sockaddr *)&address, addressLength) != 0) {
    close(descriptor);
    return -1;
  }
  if (chmod(rawPath, 0600) != 0 || listen(descriptor, 1) != 0) {
    close(descriptor);
    unlink(rawPath);
    return -1;
  }
  if (lstat(rawPath, boundMetadata) != 0 || !S_ISSOCK(boundMetadata->st_mode)) {
    close(descriptor);
    unlink(rawPath);
    return -1;
  }
  return descriptor;
}

static void writeBigEndian16(uint8_t *destination, uint16_t value) {
  destination[0] = (uint8_t)((value >> 8) & 0xff);
  destination[1] = (uint8_t)(value & 0xff);
}

static void writeBigEndian32(uint8_t *destination, uint32_t value) {
  destination[0] = (uint8_t)((value >> 24) & 0xff);
  destination[1] = (uint8_t)((value >> 16) & 0xff);
  destination[2] = (uint8_t)((value >> 8) & 0xff);
  destination[3] = (uint8_t)(value & 0xff);
}

static BOOL sendHandoffFrame(int ownerDescriptor, int guestDescriptor, uint32_t guestPort) {
  /*
   * SOCK_STREAM has no packet boundary.  The receiver contract is to use
   * recvmsg with a kHandoffFrameBytes + 1 payload window and MSG_WAITALL,
   * require exactly kHandoffFrameBytes after the peer closes its write side,
   * inspect exactly one SCM_RIGHTS descriptor, and reject any trailing byte
   * or MSG_CTRUNC before accepting the handoff.
   */
  uint8_t frame[kHandoffFrameBytes] = {
    'D', 'L', 'V', 'Z',
    kHandoffFrameVersion,
    kHandoffFrameType,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    0,
  };
  writeBigEndian16(frame + 6, (uint16_t)kHandoffFrameBytes);
  writeBigEndian32(frame + 8, guestPort);

  struct iovec vector = {
    .iov_base = frame,
    .iov_len = sizeof(frame),
  };
  union {
    struct cmsghdr header;
    uint8_t bytes[CMSG_SPACE(sizeof(int))];
  } control = {0};
  struct msghdr message = {0};
  message.msg_iov = &vector;
  message.msg_iovlen = 1;
  message.msg_control = control.bytes;
  message.msg_controllen = sizeof(control.bytes);

  struct cmsghdr *header = CMSG_FIRSTHDR(&message);
  if (header == NULL) {
    return NO;
  }
  header->cmsg_len = CMSG_LEN(sizeof(int));
  header->cmsg_level = SOL_SOCKET;
  header->cmsg_type = SCM_RIGHTS;
  memcpy(CMSG_DATA(header), &guestDescriptor, sizeof(guestDescriptor));

  ssize_t sent;
  do {
    sent = sendmsg(ownerDescriptor, &message, 0);
  } while (sent < 0 && errno == EINTR);
  return sent == (ssize_t)sizeof(frame);
}

@interface SlotController : NSObject <VZVirtualMachineDelegate>
@property(nonatomic, strong) SlotOptions *options;
@property(nonatomic, strong) VZVirtualMachine *machine;
@property(nonatomic, strong) VZVirtioSocketConnection *guestConnection;
@property(nonatomic, strong) dispatch_source_t timeoutSource;
@property(nonatomic, strong) dispatch_source_t handoffSource;
@property(nonatomic, strong) dispatch_source_t sigtermSource;
@property(nonatomic, strong) dispatch_source_t sigintSource;
@property(nonatomic, assign) int handoffListener;
@property(nonatomic, assign) struct stat handoffMetadata;
@property(nonatomic, assign) BOOL handoffPathCreated;
@property(nonatomic, assign) BOOL handoffDelivered;
@property(nonatomic, assign) NSUInteger handoffConnectionCount;
@property(nonatomic, assign) NSUInteger guestConnectAttempts;
@property(nonatomic, assign) BOOL stopInFlight;
@property(nonatomic, assign) BOOL finished;
@property(nonatomic, assign) StopReason stopReason;
@property(nonatomic, copy) NSString *failureEvent;
@property(nonatomic, assign) int failureExitCode;
@end

@implementation SlotController

- (instancetype)initWithOptions:(SlotOptions *)options machine:(VZVirtualMachine *)machine {
  self = [super init];
  if (self != nil) {
    _options = options;
    _machine = machine;
    _handoffListener = -1;
    _failureExitCode = kExitRuntime;
  }
  return self;
}

- (void)installSignalSources {
  signal(SIGTERM, SIG_IGN);
  signal(SIGINT, SIG_IGN);

  dispatch_queue_t queue = dispatch_get_main_queue();
  __weak SlotController *weakSelf = self;
  self.sigtermSource = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, SIGTERM, 0, queue);
  dispatch_source_set_event_handler(self.sigtermSource, ^{
    [weakSelf requestStopForReason:StopReasonSignal failureEvent:nil failureExitCode:0];
  });
  dispatch_resume(self.sigtermSource);

  self.sigintSource = dispatch_source_create(DISPATCH_SOURCE_TYPE_SIGNAL, SIGINT, 0, queue);
  dispatch_source_set_event_handler(self.sigintSource, ^{
    [weakSelf requestStopForReason:StopReasonSignal failureEvent:nil failureExitCode:0];
  });
  dispatch_resume(self.sigintSource);
}

- (BOOL)prepareHandoffListener {
  if (!self.options.hasGuestControlPort) {
    return YES;
  }
  self.handoffListener = createHandoffListener(self.options.handoffSocketPath, &_handoffMetadata);
  if (self.handoffListener < 0) {
    return NO;
  }
  self.handoffPathCreated = YES;

  return YES;
}

- (void)installHandoffSource {
  if (self.handoffListener < 0 || self.handoffSource != nil) {
    return;
  }
  __weak SlotController *weakSelf = self;
  self.handoffSource = dispatch_source_create(DISPATCH_SOURCE_TYPE_READ, (uintptr_t)self.handoffListener, 0, dispatch_get_main_queue());
  dispatch_source_set_event_handler(self.handoffSource, ^{
    [weakSelf acceptHandoffConnection];
  });
  dispatch_resume(self.handoffSource);
}

- (void)startTimeout {
  __weak SlotController *weakSelf = self;
  self.timeoutSource = dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER, 0, 0, dispatch_get_main_queue());
  dispatch_source_set_timer(self.timeoutSource, dispatch_time(DISPATCH_TIME_NOW, (int64_t)self.options.timeoutSeconds * NSEC_PER_SEC), DISPATCH_TIME_FOREVER, 0);
  dispatch_source_set_event_handler(self.timeoutSource, ^{
    [weakSelf requestStopForReason:StopReasonTimeout failureEvent:nil failureExitCode:kExitTimeout];
  });
  dispatch_resume(self.timeoutSource);
}

- (void)start {
  if (![self prepareHandoffListener]) {
    [self finishWithEvent:@"handoff_setup_failed" status:@"failed" exitCode:kExitConfiguration];
    return;
  }
  [self installSignalSources];
  [self startTimeout];

  __weak SlotController *weakSelf = self;
  @try {
    [self.machine startWithCompletionHandler:^(NSError *error) {
      SlotController *strongSelf = weakSelf;
      if (strongSelf == nil || strongSelf.finished) {
        return;
      }
      if (error != nil) {
        [strongSelf finishWithEvent:@"vm_start_failed" status:@"failed" exitCode:kExitStart];
        return;
      }

      if (strongSelf.stopReason != StopReasonNone) {
        [strongSelf stopMachine];
        return;
      }
      emitReceipt(@{
        @"kind": @"native_lifecycle",
        @"event": @"vm_started",
        @"status": @"running",
      });
      if (strongSelf.options.hasGuestControlPort) {
        [strongSelf connectGuestControl];
      }
    }];
  } @catch (__unused NSException *exception) {
    [self finishWithEvent:@"vm_start_failed" status:@"failed" exitCode:kExitStart];
  }
}

- (void)connectGuestControl {
  if (self.finished || self.stopReason != StopReasonNone) return;
  self.guestConnectAttempts += 1;
  VZVirtioSocketDevice *socketDevice = nil;
  for (VZSocketDevice *device in self.machine.socketDevices) {
    if ([device isKindOfClass:[VZVirtioSocketDevice class]]) {
      socketDevice = (VZVirtioSocketDevice *)device;
      break;
    }
  }
  if (socketDevice == nil) {
    [self requestStopForReason:StopReasonFailure failureEvent:@"vsock_device_unavailable" failureExitCode:kExitRuntime];
    return;
  }

  __weak SlotController *weakSelf = self;
  [socketDevice connectToPort:self.options.guestControlPort completionHandler:^(VZVirtioSocketConnection *connection, NSError *error) {
    SlotController *strongSelf = weakSelf;
    if (strongSelf == nil || strongSelf.finished) {
      [connection close];
      return;
    }
    if (error != nil || connection == nil || connection.fileDescriptor < 0) {
      [connection close];
      // VM start precedes guest boot and the fixed service becoming ready.
      // Only this zero-payload connection probe is retried, for at most 30s.
      if (strongSelf.guestConnectAttempts < 300 && strongSelf.stopReason == StopReasonNone) {
        dispatch_after(dispatch_time(DISPATCH_TIME_NOW, NSEC_PER_SEC / 10), dispatch_get_main_queue(), ^{
          [strongSelf connectGuestControl];
        });
        return;
      }
      [strongSelf requestStopForReason:StopReasonFailure failureEvent:@"guest_control_connect_failed" failureExitCode:kExitRuntime];
      return;
    }
    strongSelf.guestConnection = connection;
    [strongSelf installHandoffSource];
    emitReceipt(@{
      @"kind": @"native_lifecycle",
      @"event": @"vsock_connected",
      @"status": @"connected",
      @"guest_control_port": @(strongSelf.options.guestControlPort),
      @"connect_attempts": @(strongSelf.guestConnectAttempts),
    });
  }];
}

- (void)acceptHandoffConnection {
  if (self.finished || self.handoffListener < 0 || self.handoffConnectionCount >= 1) {
    return;
  }

  int ownerDescriptor = accept(self.handoffListener, NULL, NULL);
  if (ownerDescriptor < 0) {
    if (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK) {
      return;
    }
    [self requestStopForReason:StopReasonFailure failureEvent:@"handoff_accept_failed" failureExitCode:kExitRuntime];
    return;
  }
  self.handoffConnectionCount += 1;
  int noSignal = 1;
  (void)setsockopt(ownerDescriptor, SOL_SOCKET, SO_NOSIGPIPE, &noSignal, sizeof(noSignal));
  if (!ownerPeerMatches(ownerDescriptor)) {
    close(ownerDescriptor);
    [self requestStopForReason:StopReasonFailure failureEvent:@"handoff_owner_mismatch" failureExitCode:kExitRuntime];
    return;
  }
  int guestDescriptor = self.guestConnection.fileDescriptor;
  BOOL delivered = guestDescriptor >= 0 && sendHandoffFrame(ownerDescriptor, guestDescriptor, self.options.guestControlPort);
  close(ownerDescriptor);

  if (!delivered) {
    [self requestStopForReason:StopReasonFailure failureEvent:@"handoff_frame_failed" failureExitCode:kExitRuntime];
    return;
  }

  self.handoffDelivered = YES;
  [self closeHandoffListener];
  emitReceipt(@{
    @"kind": @"native_lifecycle",
    @"event": @"fd_handoff_delivered",
    @"status": @"connected",
    @"guest_control_port": @(self.options.guestControlPort),
    @"handoff_connections": @(self.handoffConnectionCount),
  });
}

- (void)requestStopForReason:(StopReason)reason failureEvent:(NSString *)failureEvent failureExitCode:(int)failureExitCode {
  if (self.finished || self.stopReason != StopReasonNone) {
    return;
  }
  self.stopReason = reason;
  if (failureEvent != nil) {
    self.failureEvent = failureEvent;
    self.failureExitCode = failureExitCode == 0 ? kExitRuntime : failureExitCode;
    emitReceipt(@{
      @"kind": @"native_lifecycle",
      @"event": failureEvent,
      @"status": @"failed",
    });
  }
  if (reason == StopReasonTimeout) {
    emitReceipt(@{
      @"kind": @"native_lifecycle",
      @"event": @"timeout",
      @"status": @"expired",
      @"timeout_seconds": @(self.options.timeoutSeconds),
    });
  }
  [self stopMachine];
}

- (void)stopMachine {
  if (self.finished || self.stopInFlight) {
    return;
  }
  self.stopInFlight = YES;
  StopReason reason = self.stopReason;
  __weak SlotController *weakSelf = self;
  @try {
    [self.machine stopWithCompletionHandler:^(NSError *error) {
      SlotController *strongSelf = weakSelf;
      if (strongSelf == nil || strongSelf.finished) {
        return;
      }
      if (error != nil) {
        [strongSelf finishWithEvent:@"vm_stop_failed" status:@"failed" exitCode:(reason == StopReasonTimeout ? kExitTimeout : kExitRuntime)];
        return;
      }
      if (reason == StopReasonTimeout) {
        [strongSelf finishWithEvent:@"forced_stop" status:@"timeout" exitCode:kExitTimeout];
      } else if (reason == StopReasonSignal) {
        [strongSelf finishWithEvent:@"stopped" status:@"signal" exitCode:EXIT_SUCCESS];
      } else {
        [strongSelf finishWithEvent:@"stopped_after_failure" status:@"failed" exitCode:strongSelf.failureExitCode];
      }
    }];
  } @catch (__unused NSException *exception) {
    [self finishWithEvent:@"vm_stop_failed" status:@"failed" exitCode:(reason == StopReasonTimeout ? kExitTimeout : kExitRuntime)];
  }
}

- (void)finishWithEvent:(NSString *)event status:(NSString *)status exitCode:(int)exitCode {
  if (self.finished) {
    return;
  }
  self.finished = YES;
  [self closeHandoffListener];
  if (self.timeoutSource != nil) {
    dispatch_source_cancel(self.timeoutSource);
    self.timeoutSource = nil;
  }
  if (self.sigtermSource != nil) {
    dispatch_source_cancel(self.sigtermSource);
    self.sigtermSource = nil;
  }
  if (self.sigintSource != nil) {
    dispatch_source_cancel(self.sigintSource);
    self.sigintSource = nil;
  }
  signal(SIGTERM, SIG_DFL);
  signal(SIGINT, SIG_DFL);
  [self.guestConnection close];
  self.guestConnection = nil;

  NSMutableDictionary *fields = [@{
    @"kind": @"native_lifecycle",
    @"event": event,
    @"status": status,
    @"handoff_connections": @(self.handoffConnectionCount),
  } mutableCopy];
  if (self.options.hasGuestControlPort) {
    fields[@"guest_control_port"] = @(self.options.guestControlPort);
    fields[@"fd_handoff_delivered"] = @(self.handoffDelivered);
  }
  emitReceipt(fields);
  exit(exitCode);
}

- (void)closeHandoffListener {
  if (self.handoffSource != nil) {
    dispatch_source_cancel(self.handoffSource);
    self.handoffSource = nil;
  }
  if (self.handoffListener >= 0) {
    close(self.handoffListener);
    self.handoffListener = -1;
  }
  if (!self.handoffPathCreated) {
    return;
  }
  struct stat current;
  const char *rawPath = self.options.handoffSocketPath.fileSystemRepresentation;
  if (lstat(rawPath, &current) == 0 && current.st_dev == self.handoffMetadata.st_dev && current.st_ino == self.handoffMetadata.st_ino) {
    unlink(rawPath);
  }
  self.handoffPathCreated = NO;
}

- (void)guestDidStopVirtualMachine:(VZVirtualMachine *)virtualMachine {
  (void)virtualMachine;
  if (self.finished || self.stopInFlight) {
    return;
  }
  if (self.options.hasGuestControlPort && !self.handoffDelivered) {
    [self finishWithEvent:@"guest_stopped_without_handoff" status:@"failed" exitCode:kExitRuntime];
    return;
  }
  [self finishWithEvent:@"guest_stopped" status:@"guest_normal" exitCode:EXIT_SUCCESS];
}

- (void)virtualMachine:(VZVirtualMachine *)virtualMachine didStopWithError:(NSError *)error {
  (void)virtualMachine;
  (void)error;
  if (self.finished || self.stopInFlight) {
    return;
  }
  [self finishWithEvent:@"vm_failed" status:@"failed" exitCode:kExitRuntime];
}

@end

int main(int argc, const char *argv[]) {
  @autoreleasepool {
    SlotOptions *options = nil;
    NSString *failureReason = @"invalid_argument";
    ParseResult parseResult = parseOptions(argc, argv, &options, &failureReason);
    if (parseResult == ParseResultHelp) {
      printUsage();
      return EXIT_SUCCESS;
    }
    if (parseResult == ParseResultInvalid || options == nil) {
      return emitConfigurationFailure(failureReason, kExitUsage);
    }

    VZVirtualMachineConfiguration *configuration = makeConfiguration(options);
    if (options.validationOnly) {
      emitConfigurationReceipt(options, configuration, YES);
      return EXIT_SUCCESS;
    }

    NSError *validationError = nil;
    if (![configuration validateWithError:&validationError]) {
      (void)validationError;
      return emitConfigurationFailure(@"virtualization_configuration_invalid", kExitConfiguration);
    }

    emitConfigurationReceipt(options, configuration, NO);
    VZVirtualMachine *machine = [[VZVirtualMachine alloc] initWithConfiguration:configuration];
    SlotController *controller = [[SlotController alloc] initWithOptions:options machine:machine];
    machine.delegate = controller;
    [controller start];
    [[NSRunLoop mainRunLoop] run];
    return kExitRuntime;
  }
}
