/* Build compatibility for the vendor source with older kernel headers.
 * These headers use the compiler's stdarg.h rather than linux/stdarg.h.
 * No driver logic or userspace/wire ABI is changed.
 */
#ifndef BRI_GUEST_KERNEL_STDARG_H
#define BRI_GUEST_KERNEL_STDARG_H
#include <stdarg.h>
#endif
