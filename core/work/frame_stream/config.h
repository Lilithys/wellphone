/* macOS/Homebrew client-only build; matches scrcpy 4.1 Meson options. */
#define HAVE_STRDUP 1
#define HAVE_ASPRINTF 1
#define HAVE_VASPRINTF 1
#define HAVE_NRAND48 1
#define HAVE_JRAND48 1
#define SCRCPY_VERSION "4.1"
#define PREFIX "/opt/homebrew"
#define DEFAULT_LOCAL_PORT_RANGE_FIRST 27183
#define DEFAULT_LOCAL_PORT_RANGE_LAST 27199
/* USB/OTG, V4L2, server debugger and PORTABLE are intentionally disabled. */
