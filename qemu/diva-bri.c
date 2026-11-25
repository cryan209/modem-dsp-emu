/* SPDX-License-Identifier: GPL-2.0-or-later */
/* PCI transport only: all Diva board/firmware behavior lives in BriCard. */
#include "qemu/osdep.h"
#include "qemu/bswap.h"
#include "qemu/cutils.h"
#include "qemu/error-report.h"
#include "qemu/module.h"
#include "qemu/sockets.h"
#include "qemu/timer.h"
#include "qapi/error.h"
#include "hw/pci/pci_device.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "system/runstate.h"
#include <sys/un.h>

#define TYPE_DIVA_BRI "diva-bri"
OBJECT_DECLARE_SIMPLE_TYPE(DivaBri, DIVA_BRI)
#define BRI_MAGIC 0x42524931
enum { BRI_HELLO, BRI_READ, BRI_WRITE, BRI_ADVANCE,
       BRI_CONFIG_READ, BRI_CONFIG_WRITE, BRI_RESET };

typedef struct DivaBar {
    MemoryRegion region;
    DivaBri *board;
    unsigned int index;
} DivaBar;

struct DivaBri {
    PCIDevice parent_obj;
    DivaBar bars[4];
    MemoryRegion shared_ram;
    MemoryRegion ram_fallback;
    QEMUTimer *timer;
    char *socket_path;
    char *ram_file;
    int fd;
    bool failed;
    bool ready;
    uint32_t flags;
    uint32_t step_instructions;
    uint64_t step_ns;
};

static bool bri_rpc(DivaBri *s, uint8_t op, uint8_t bar, uint16_t size,
                    uint32_t offset, uint64_t value, uint64_t *result)
{
    uint8_t request[20] = { 0 }, reply[24];
    size_t received = 0;
    if (s->failed) {
        return false;
    }
    stl_le_p(request, BRI_MAGIC);
    request[4] = op;
    request[5] = bar;
    stw_le_p(request + 6, size);
    stl_le_p(request + 8, offset);
    stq_le_p(request + 12, value);
    if (qemu_write_full(s->fd, request, sizeof(request)) != sizeof(request)) {
        goto fail;
    }
    while (received < sizeof(reply)) {
        ssize_t n = recv(s->fd, reply + received, sizeof(reply) - received, 0);
        if (n < 0 && errno == EINTR) {
            continue;
        }
        if (n <= 0) {
            goto fail;
        }
        received += n;
    }
    if (ldl_le_p(reply) != BRI_MAGIC || ldl_le_p(reply + 4) != 0 ||
        ldl_le_p(reply + 20) != 0) {
        goto fail;
    }
    s->flags = ldl_le_p(reply + 16);
    if (s->ready) {
        pci_set_irq(PCI_DEVICE(s), (s->flags & 1) &&
                    !(pci_get_word(PCI_DEVICE(s)->config + 0x44) & 3));
        if (s->ram_file) {
            /* A PLX reset removes local decode until EEPROM reload. */
            memory_region_set_enabled(&s->shared_ram, !(s->flags & 8));
        }
    }
    if (result) {
        *result = ldq_le_p(reply + 8);
    }
    return true;
fail:
    s->failed = true;
    error_report("diva-bri backend failed: op=%u BAR%u offset=0x%x size=%u",
                 op, bar, offset, size);
    qemu_system_vmstop_request(RUN_STATE_INTERNAL_ERROR);
    return false;
}

static uint64_t bri_read(void *opaque, hwaddr offset, unsigned size)
{
    DivaBar *bar = opaque;
    uint64_t value = UINT64_MAX;
    bri_rpc(bar->board, BRI_READ, bar->index, size, offset, 0, &value);
    return value;
}

static void bri_write(void *opaque, hwaddr offset, uint64_t value, unsigned size)
{
    DivaBar *bar = opaque;
    bri_rpc(bar->board, BRI_WRITE, bar->index, size, offset, value, NULL);
}

static const MemoryRegionOps bri_ops = {
    .read = bri_read,
    .write = bri_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8 },
    /* Split wide CPU accesses into actual supported PCI transactions. */
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static uint32_t bri_config_read(PCIDevice *pdev, uint32_t offset, int size)
{
    DivaBri *s = DIVA_BRI(pdev);
    uint64_t value = UINT32_MAX;
    /* VPD is device-owned; ordinary PCI decoding stays in QEMU. */
    if (offset >= 0x4c && offset + size <= 0x54) {
        bri_rpc(s, BRI_CONFIG_READ, 0, size, offset, 0, &value);
        return value;
    }
    return pci_default_read_config(pdev, offset, size);
}

static void bri_config_write(PCIDevice *pdev, uint32_t offset,
                             uint32_t value, int size)
{
    DivaBri *s = DIVA_BRI(pdev);
    pci_default_write_config(pdev, offset, value, size);
    bri_rpc(s, BRI_CONFIG_WRITE, 0, size, offset, value, NULL);
}

static void bri_tick(void *opaque)
{
    DivaBri *s = opaque;
    if ((s->flags & 2) &&
        !bri_rpc(s, BRI_ADVANCE, 0, 0, 0, s->step_instructions, NULL)) {
        return;
    }
    timer_mod(s->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->step_ns);
}

static void bri_reset(DeviceState *dev)
{
    DivaBri *s = DIVA_BRI(dev);
    s->failed = false;
    bri_rpc(s, BRI_RESET, 0, 0, 0, 0, NULL);
    timer_mod(s->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->step_ns);
}

static void bri_realize(PCIDevice *pdev, Error **errp)
{
    static const uint32_t sizes[] = { 0x100, 0x100, 0x1000000, 0x10000 };
    DivaBri *s = DIVA_BRI(pdev);
    struct sockaddr_un address = { .sun_family = AF_UNIX };
    uint64_t version;
    if (!s->socket_path || strlen(s->socket_path) >= sizeof(address.sun_path) ||
        !s->step_ns || !s->step_instructions || s->step_instructions > 1000000) {
        error_setg(errp, "diva-bri requires a local socket and valid clock steps");
        return;
    }
    pstrcpy(address.sun_path, sizeof(address.sun_path), s->socket_path);
    s->fd = qemu_socket(AF_UNIX, SOCK_STREAM, 0);
    if (s->fd < 0 || connect(s->fd, (struct sockaddr *)&address, sizeof(address))) {
        error_setg_errno(errp, errno, "cannot connect diva-bri backend");
        if (s->fd >= 0) {
            close(s->fd);
            s->fd = -1;
        }
        return;
    }
    if (!bri_rpc(s, BRI_HELLO, 0, 0, 0, 1, &version) || version != 1) {
        error_setg(errp, "incompatible diva-bri backend");
        close(s->fd);
        s->fd = -1;
        return;
    }
    pci_config_set_interrupt_pin(pdev->config, 1);
    /* Insert in reverse order to retain the measured 40 -> 48 -> 4c chain. */
    if (pci_add_capability(pdev, PCI_CAP_ID_VPD, 0x4c, 8, errp) < 0 ||
        pci_add_capability(pdev, PCI_CAP_ID_CHSWP, 0x48, 4, errp) < 0 ||
        pci_pm_init(pdev, 0x40, errp) < 0) {
        close(s->fd);
        s->fd = -1;
        return;
    }
    pci_set_word(pdev->config + 0x42, 1);
    /* Measured PMC: D0/D3hot, no D1/D2 or PME support. */
    pdev->wmask[0x44] = PCI_PM_CTRL_STATE_MASK;
    pdev->config[0x4a] = 0x80;
    pci_set_word(pdev->config + PCI_STATUS, 0x0290);
    for (unsigned i = 0; i < 4; i++) {
        char *name = g_strdup_printf("diva-bri-bar%u", i);
        s->bars[i].board = s;
        s->bars[i].index = i;
        if (i == 2 && s->ram_file) {
            memory_region_init(&s->bars[i].region, OBJECT(s), name, sizes[i]);
            memory_region_init_io(&s->ram_fallback, OBJECT(s), &bri_ops,
                                  &s->bars[i], "diva-bri-sdram-fallback", sizes[i]);
            memory_region_add_subregion(&s->bars[i].region, 0, &s->ram_fallback);
            if (!memory_region_init_ram_from_file(&s->shared_ram, OBJECT(s),
                    "diva-bri-shared-sdram", sizes[i], 0, RAM_SHARED, s->ram_file, 0, errp)) {
                g_free(name);
                close(s->fd);
                s->fd = -1;
                return;
            }
            memory_region_add_subregion_overlap(&s->bars[i].region, 0,
                                                &s->shared_ram, 1);
        } else {
            memory_region_init_io(&s->bars[i].region, OBJECT(s), &bri_ops,
                                  &s->bars[i], name, sizes[i]);
        }
        g_free(name);
        pci_register_bar(pdev, i, i == 1 ? PCI_BASE_ADDRESS_SPACE_IO :
                         PCI_BASE_ADDRESS_SPACE_MEMORY, &s->bars[i].region);
    }
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, bri_tick, s);
    timer_mod(s->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->step_ns);
    s->ready = true;
}

static void bri_exit(PCIDevice *pdev)
{
    DivaBri *s = DIVA_BRI(pdev);
    if (s->timer) {
        timer_free(s->timer);
    }
    if (s->fd >= 0) {
        close(s->fd);
    }
}

static void bri_init(Object *obj)
{
    DIVA_BRI(obj)->fd = -1;
}

static const Property bri_properties[] = {
    DEFINE_PROP_STRING("socket", DivaBri, socket_path),
    DEFINE_PROP_STRING("ram-file", DivaBri, ram_file),
    DEFINE_PROP_UINT32("step-instructions", DivaBri, step_instructions, 50000),
    DEFINE_PROP_UINT64("step-ns", DivaBri, step_ns, 1000000),
};

static const VMStateDescription bri_vmstate = {
    .name = TYPE_DIVA_BRI,
    .unmigratable = true,
};

static void bri_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    PCIDeviceClass *pc = PCI_DEVICE_CLASS(klass);
    pc->realize = bri_realize;
    pc->exit = bri_exit;
    pc->config_read = bri_config_read;
    pc->config_write = bri_config_write;
    pc->vendor_id = 0x1133;
    pc->device_id = 0xe013;
    pc->subsystem_vendor_id = 0x8001;
    pc->subsystem_id = 0x0014;
    pc->revision = 1;
    pc->class_id = 0x0280;
    dc->desc = "Diva 4BRI v2 whole-board emulator bridge";
    dc->vmsd = &bri_vmstate;
    device_class_set_legacy_reset(dc, bri_reset);
    device_class_set_props(dc, bri_properties);
    set_bit(DEVICE_CATEGORY_NETWORK, dc->categories);
}

static const TypeInfo bri_type = {
    .name = TYPE_DIVA_BRI,
    .parent = TYPE_PCI_DEVICE,
    .instance_size = sizeof(DivaBri),
    .instance_init = bri_init,
    .class_init = bri_class_init,
    .interfaces = (const InterfaceInfo[]) {
        { INTERFACE_CONVENTIONAL_PCI_DEVICE }, { }
    },
};

static void bri_register_types(void)
{
    type_register_static(&bri_type);
}
type_init(bri_register_types)
