// SPDX-License-Identifier: GPL-2.0
/* Minimal, guarded probe for the Ambient/Intel HaM 1813:4000 DSP interface. */

#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/io.h>
#include <linux/module.h>
#include <linux/pci.h>
#include <linux/unaligned.h>

#define DRV_NAME "ambient_ham_probe"

#define HAM_REG_RESET_STATUS 0x42
#define HAM_REG_DSP_CFG      0xf6
#define HAM_REG_INT_EVENT    0xf7
#define HAM_REG_PATCH_ADDR_L 0xf8
#define HAM_REG_PATCH_ADDR_H 0xf9
#define HAM_REG_PATCH_DATA   0xfa
#define HAM_REG_RESET        0xfc
#define HAM_REG_HOST_TRIGGER 0xfe
#define HAM_REG_DSP_ACK      0xff

#define HAM_CRAM_DSP_PACKET  0x00
#define HAM_CRAM_HOST_PACKET 0x40
#define HAM_PATCH_SIZE       0x747c
#define HAM_PATCH_END        0x30
#define HAM_POWERON_OVERLAY  0x00
#define HAM_POWERON_REQUEST  0x60

static bool do_reset;
module_param(do_reset, bool, 0400);
MODULE_PARM_DESC(do_reset,
	"Run the recovered DSP reset sequence (default: false/read-only)");

static bool load_patch;
module_param(load_patch, bool, 0400);
MODULE_PARM_DESC(load_patch,
	"Load power-on DSP overlay and query version (default: false)");

static char *firmware_name = "ambient/ham_patch_array.bin";
module_param(firmware_name, charp, 0400);
MODULE_PARM_DESC(firmware_name, "PatchArray firmware path");

static bool survey_speakerphone;
module_param(survey_speakerphone, bool, 0400);
MODULE_PARM_DESC(survey_speakerphone,
	"Enter and leave DSP speakerphone mode to trace overlay requests");

static int survey_mode = -1;
module_param(survey_mode, int, 0400);
MODULE_PARM_DESC(survey_mode,
	"Prepare one modulation mode (0..24) while the DAA remains on-hook");

static char *dial_number;
module_param(dial_number, charp, 0400);
MODULE_PARM_DESC(dial_number,
	"Dial a numeric playground extension and observe DSP events for 15 seconds");

static int answer_mode = -1;
module_param(answer_mode, int, 0400);
MODULE_PARM_DESC(answer_mode,
	"Answer after a five-second ring window and train in mode 0..24");

struct ham_probe {
	void __iomem *bar0;
	u16 original_pci_command;
};

static u8 ham_read(struct ham_probe *ham, unsigned int reg)
{
	return ioread8(ham->bar0 + reg);
}

static void ham_write(struct ham_probe *ham, unsigned int reg, u8 value)
{
	iowrite8(value, ham->bar0 + reg);
	/* Flush posted PCI writes through a register the legacy driver reads. */
	(void)ioread8(ham->bar0 + HAM_REG_INT_EVENT);
}

static void ham_log_registers(struct pci_dev *pdev, struct ham_probe *ham,
			      const char *label)
{
	dev_info(&pdev->dev,
		 "%s: 42=%02x f6=%02x f7=%02x f8=%02x f9=%02x fc=%02x\n",
		 label,
		 ham_read(ham, HAM_REG_RESET_STATUS),
		 ham_read(ham, HAM_REG_DSP_CFG),
		 ham_read(ham, HAM_REG_INT_EVENT),
		 ham_read(ham, HAM_REG_PATCH_ADDR_L),
		 ham_read(ham, HAM_REG_PATCH_ADDR_H),
		 ham_read(ham, HAM_REG_RESET));
}

static int ham_reset_dsp(struct pci_dev *pdev, struct ham_probe *ham)
{
	unsigned int elapsed;
	u8 status = 0xff;

	/* Exact ResetDspInternal sequence from Intel-v92ham-453/hamcore.lib. */
	ham_write(ham, HAM_REG_RESET, 0x02);
	ham_write(ham, HAM_REG_INT_EVENT, 0x00);
	ham_write(ham, HAM_REG_DSP_CFG,
		  ham_read(ham, HAM_REG_DSP_CFG) | 0x01);
	ham_write(ham, HAM_REG_RESET_STATUS, 0x01);
	ham_write(ham, HAM_REG_RESET, 0x01);

	for (elapsed = 0; elapsed < 500; elapsed++) {
		msleep(1);
		status = ham_read(ham, HAM_REG_RESET_STATUS);
		if (status == 0)
			break;
	}

	if (status != 0) {
		dev_err(&pdev->dev,
			"DSP reset timed out after 500 ms (42=%02x)\n", status);
		return -ETIMEDOUT;
	}

	msleep(150);
	dev_info(&pdev->dev, "DSP reset completed after %u ms\n", elapsed + 1);
	return 0;
}

static void ham_quiesce(struct pci_dev *pdev, struct ham_probe *ham)
{
	u8 value;

	/* Exact set_hook_relay(0) GPIO latch sequence: force on-hook. */
	value = ham_read(ham, 0xf0) | 0x04;
	ham_write(ham, 0xf0, value);
	value = ham_read(ham, 0xf3) & ~0x04;
	ham_write(ham, 0xf3, value);
	value = ham_read(ham, 0xf2) | 0x04;
	ham_write(ham, 0xf2, value);
	/* Mask DSP interrupts, acknowledge any pending source, and hold reset. */
	ham_write(ham, HAM_REG_INT_EVENT, 0x00);
	(void)ham_read(ham, HAM_REG_DSP_ACK);
	ham_write(ham, HAM_REG_RESET, 0x0a);
	dev_info(&pdev->dev, "DSP interrupts masked and DSP held in reset\n");
}

static void ham_set_hook(struct ham_probe *ham, bool off_hook)
{
	u8 value;

	value = ham_read(ham, 0xf0) | 0x04;
	ham_write(ham, 0xf0, value);
	value = ham_read(ham, 0xf3) & ~0x04;
	ham_write(ham, 0xf3, value);
	value = ham_read(ham, 0xf2);
	value = off_hook ? value & ~0x04 : value | 0x04;
	ham_write(ham, 0xf2, value);
}

static int ham_wait_reset(struct pci_dev *pdev, struct ham_probe *ham)
{
	unsigned int elapsed;
	u8 status = 0xff;

	for (elapsed = 0; elapsed < 500; elapsed++) {
		msleep(1);
		status = ham_read(ham, HAM_REG_RESET_STATUS);
		if (status == 0)
			break;
	}
	if (status != 0)
		return dev_err_probe(&pdev->dev, -ETIMEDOUT,
				     "DSP release timed out (42=%02x)\n", status);
	msleep(150);
	dev_info(&pdev->dev, "DSP release completed after %u ms\n", elapsed + 1);
	return 0;
}

static int ham_put_command(struct pci_dev *pdev, struct ham_probe *ham,
			   const u8 *command, size_t command_size)
{
	size_t wire_size, i;
	unsigned int elapsed;

	if (command_size < 4 || command_size > 64 || command[2] + 4 > command_size)
		return -EINVAL;
	for (elapsed = 0; elapsed < 1000; elapsed++) {
		if (ham_read(ham, HAM_CRAM_HOST_PACKET) == 0)
			break;
		msleep(1);
	}
	if (elapsed == 1000)
		return dev_err_probe(&pdev->dev, -ETIMEDOUT,
				     "host CRAM busy for command %02x\n", command[0]);

	wire_size = command[2] + 4;
	if (wire_size & 1)
		wire_size++;
	for (i = 1; i < wire_size; i++)
		ham_write(ham, HAM_CRAM_HOST_PACKET + i, command[i]);
	/* Command byte is the ownership flag and must be published last. */
	ham_write(ham, HAM_CRAM_HOST_PACKET, command[0]);
	ham_write(ham, HAM_REG_HOST_TRIGGER, 0x01);
	return 0;
}

static int ham_get_packet(struct pci_dev *pdev, struct ham_probe *ham,
			  u8 *packet, size_t packet_size)
{
	size_t i, received;
	unsigned int elapsed;
	u8 event;

	for (elapsed = 0; elapsed < 1000; elapsed++) {
		msleep(1);
		event = ham_read(ham, HAM_REG_INT_EVENT);
		if ((event & 0x01) || ham_read(ham, HAM_CRAM_DSP_PACKET) != 0)
			break;
	}
	if (elapsed == 1000)
		return -ETIMEDOUT;

	received = min_t(size_t, ham_read(ham, 2) + 4, packet_size);
	for (i = 0; i < received; i++)
		packet[i] = ham_read(ham, i);
	/* Match dspdrv_GetCRAM and dspdrv_clear_dsp_interrupt. */
	ham_write(ham, HAM_CRAM_DSP_PACKET, 0x00);
	(void)ham_read(ham, HAM_REG_DSP_ACK);
	dev_info(&pdev->dev, "DSP packet (%zu bytes): %*ph\n",
		 received, (int)received, packet);
	return received;
}

static int ham_service_overlay(struct pci_dev *pdev, struct ham_probe *ham,
			       const struct firmware *fw, u8 request, u8 overlay);

static int ham_send_and_wait(struct pci_dev *pdev, struct ham_probe *ham,
			     const struct firmware *fw, const u8 *command,
			     size_t command_size, bool want_response,
			     u8 *result, size_t result_size)
{
	u8 packet[64];
	int ret;

	ret = ham_put_command(pdev, ham, command, command_size);
	if (ret)
		return ret;
	for (;;) {
		memset(packet, 0, sizeof(packet));
		ret = ham_get_packet(pdev, ham, packet, sizeof(packet));
		if (ret == -ETIMEDOUT)
			return dev_err_probe(&pdev->dev, ret,
					     "command %02x timed out\n", command[0]);
		if (ret < 0)
			return ret;
		if (packet[0] == 0xd6 && ret >= 6) {
			ret = ham_service_overlay(pdev, ham, fw, packet[4], packet[5]);
			if (ret)
				return ret;
			continue;
		}
		if (packet[0] == 0xc0 && ret >= 5 && packet[4] == command[0]) {
			if (!want_response)
				return 0;
			continue;
		}
		if (packet[0] == 0xc0)
			continue;
		/* These are the unsolicited cases in cmd_packet_callback(). */
		if (packet[0] == 0xc4 || packet[0] == 0xce || packet[0] == 0xcf ||
		    packet[0] == 0xd1 || packet[0] == 0xd4 || packet[0] == 0xd5 ||
		    packet[0] == 0xd7)
			continue;
		if (want_response) {
			size_t copied = min_t(size_t, ret, result_size);

			memcpy(result, packet, copied);
			return copied;
		}
	}
}

static int ham_observe(struct pci_dev *pdev, struct ham_probe *ham,
		       const struct firmware *fw, unsigned int seconds)
{
	unsigned long end = jiffies + seconds * HZ;
	u8 packet[64];
	int ret;

	while (time_before(jiffies, end)) {
		ret = ham_get_packet(pdev, ham, packet, sizeof(packet));
		if (ret == -ETIMEDOUT)
			continue;
		if (ret < 0)
			return ret;
		if (packet[0] == 0xd6 && ret >= 6) {
			ret = ham_service_overlay(pdev, ham, fw, packet[4], packet[5]);
			if (ret)
				return ret;
		}
	}
	return 0;
}

static int ham_dial_playground(struct pci_dev *pdev, struct ham_probe *ham,
			       const struct firmware *fw, const char *number)
{
	u8 response[64];
	const u8 call_progress[] = { 0x65, 0x00, 0x02, 0x00, 0x01, 0x01 };
	const u8 dial_config[] = { 0x66, 0x00, 0x06, 0x00,
				   100, 100, 0, 0, 100, 0 };
	size_t i, length = strlen(number);
	int ret;

	if (!length || length > 16)
		return -EINVAL;
	for (i = 0; i < length; i++)
		if (number[i] < '0' || number[i] > '9')
			return -EINVAL;

	dev_info(&pdev->dev, "playground call: off-hook, dialing %s\n", number);
	ham_set_hook(ham, true);
	msleep(500);
	ret = ham_send_and_wait(pdev, ham, fw, call_progress,
				sizeof(call_progress),
				false, response, sizeof(response));
	if (ret)
		goto on_hook;
	ret = ham_send_and_wait(pdev, ham, fw, dial_config, sizeof(dial_config),
				false, response, sizeof(response));
	if (ret)
		goto on_hook;
	for (i = 0; i < length; i++) {
		u8 digit[] = { 0x67, 0x00, 0x01, 0x00,
				(u8)(number[i] == '0' ? 0 : number[i] - '0') };

		ret = ham_send_and_wait(pdev, ham, fw, digit, sizeof(digit),
					false, response, sizeof(response));
		if (ret)
			goto on_hook;
		msleep(250);
	}
	dev_info(&pdev->dev, "playground call: observing for 15 seconds\n");
	ret = ham_observe(pdev, ham, fw, 15);

on_hook:
	ham_set_hook(ham, false);
	dev_info(&pdev->dev, "playground call: on-hook\n");
	return ret;
}

static int ham_answer_playground(struct pci_dev *pdev, struct ham_probe *ham,
				 const struct firmware *fw, int mode)
{
	u8 response[64];
	/* ModemSetTxLevel(2): conservative -10 dBm data/guard levels. */
	const u8 tx_level[] = { 0x7b, 0x00, 0x04, 0x00,
				10, 10, 0, 0 };
	u8 connect[] = { 0x68, 0x00, 0x06, 0x00,
			 0x00, 0x00, 0x04, 0x00, 0x00, 0x00 };
	int ret;

	if (mode < 0 || mode > 24)
		return -EINVAL;
	/* Results from the vendor translate_to_dsp_line_rate(). */
	if (mode == 0)
		connect[4] = 0x02; /* 300 bit/s */
	else if (mode == 16)
		connect[4] = 0x09;
	connect[5] = mode;
	/* Byte 6 bit 2 is packetized from sr14 bit 7: 1 selects answer mode. */
	dev_info(&pdev->dev,
		 "playground answer: waiting on-hook for five seconds\n");
	ret = ham_observe(pdev, ham, fw, 5);
	if (ret)
		return ret;
	dev_info(&pdev->dev,
		 "playground answer: off-hook, starting mode %d\n", mode);
	ham_set_hook(ham, true);
	msleep(250);
	ret = ham_send_and_wait(pdev, ham, fw, tx_level, sizeof(tx_level),
				false, response, sizeof(response));
	if (ret)
		goto on_hook;
	ret = ham_send_and_wait(pdev, ham, fw, connect, sizeof(connect),
				false, response, sizeof(response));
	if (!ret) {
		dev_info(&pdev->dev,
			 "playground answer: observing training for 30 seconds\n");
		ret = ham_observe(pdev, ham, fw, 30);
	}
on_hook:
	ham_set_hook(ham, false);
	dev_info(&pdev->dev, "playground answer: on-hook\n");
	return ret;
}

static int ham_service_overlay(struct pci_dev *pdev, struct ham_probe *ham,
			       const struct firmware *fw, u8 request, u8 overlay)
{
	size_t offset = 0;
	unsigned int records = 0, packets = 0;
	int ret;

	while (offset < fw->size && fw->data[offset] != HAM_PATCH_END) {
		if (offset + 6 > fw->size)
			return -EINVAL;
		u8 type = fw->data[offset];
		u8 record_overlay = fw->data[offset + 1];
		u16 address = get_unaligned_le16(fw->data + offset + 2);
		u16 words = get_unaligned_le16(fw->data + offset + 4);
		size_t bytes = (size_t)words * 2, done = 0;

		if (offset + 6 + bytes > fw->size)
			return -EINVAL;
		if (record_overlay == overlay) {
			if (!(type & 0x01))
				return dev_err_probe(&pdev->dev, -EOPNOTSUPP,
					"unsupported overlay record type %02x\n", type);
			while (done < bytes) {
				u8 command[20] = { 0x0c, 0, 0, 0 };
				size_t chunk = min_t(size_t, 12, bytes - done);
				u16 chunk_address = address + done / 2;

				command[2] = chunk + 4;
				command[4] = chunk_address & 0xff;
				command[5] = chunk_address >> 8;
				command[6] = (type & 0x0e) << 4;
				memcpy(command + 8, fw->data + offset + 6 + done, chunk);
				ret = ham_put_command(pdev, ham, command, sizeof(command));
				if (ret)
					return ret;
				done += chunk;
				packets++;
			}
			records++;
		}
		offset += 6 + bytes;
	}
	if (!records)
		return dev_err_probe(&pdev->dev, -ENOENT,
				     "DSP requested absent overlay %u\n", overlay);

	dev_info(&pdev->dev,
		 "supplied request %02x overlay %u: %u records, %u packets\n",
		 request, overlay, records, packets);
	{
		const u8 loaded[] = { 0xa3, 0x00, 0x02, 0x00, overlay, request };

		/* Its ACK is drained by the already-running outer command wait. */
		return ham_put_command(pdev, ham, loaded, sizeof(loaded));
	}
}

static int ham_load_poweron_patch(struct pci_dev *pdev, struct ham_probe *ham)
{
	const struct firmware *fw;
	size_t offset = 0;
	unsigned int loaded_records = 0, loaded_words = 0;
	u8 response[64] = { 0 };
	int ret;

	ret = request_firmware_direct(&fw, firmware_name, &pdev->dev);
	if (ret)
		return dev_err_probe(&pdev->dev, ret,
				     "cannot load firmware %s\n", firmware_name);
	if (fw->size != HAM_PATCH_SIZE) {
		ret = dev_err_probe(&pdev->dev, -EINVAL,
				    "unexpected PatchArray size %zu\n", fw->size);
		goto out;
	}

	/* PutDspInReset from the original driver. */
	ham_write(ham, HAM_REG_RESET, 0x0a);
	while (offset < fw->size && fw->data[offset] != HAM_PATCH_END) {
		u8 type, overlay;
		u16 address, words;
		size_t bytes, i;

		if (offset + 6 > fw->size) {
			ret = -EINVAL;
			goto malformed;
		}
		type = fw->data[offset];
		overlay = fw->data[offset + 1];
		address = get_unaligned_le16(fw->data + offset + 2);
		words = get_unaligned_le16(fw->data + offset + 4);
		bytes = (size_t)words * 2;
		if (offset + 6 + bytes > fw->size) {
			ret = -EINVAL;
			goto malformed;
		}
		if (overlay == HAM_POWERON_OVERLAY) {
			ham_write(ham, HAM_REG_PATCH_ADDR_H, address >> 8);
			ham_write(ham, HAM_REG_PATCH_ADDR_L, address & 0xff);
			for (i = 0; i < bytes; i++)
				ham_write(ham, HAM_REG_PATCH_DATA, fw->data[offset + 6 + i]);
			loaded_records++;
			loaded_words += words;
			dev_info(&pdev->dev,
				 "loaded overlay %u type %02x at %04x (%u words)\n",
				 overlay, type, address, words);
		}
		offset += 6 + bytes;
	}
	if (offset >= fw->size || loaded_records != 3 || loaded_words != 823) {
		ret = -EINVAL;
		goto malformed;
	}

	/* ReleaseDspReset from the original driver. */
	ham_write(ham, HAM_REG_RESET_STATUS, 0x01);
	ham_write(ham, HAM_REG_RESET, 0x09);
	ret = ham_wait_reset(pdev, ham);
	if (ret)
		goto out;

	/* Notify the DSP that request 0x60/overlay 0 is loaded. */
	{
		const u8 loaded[] = { 0xa3, 0x00, 0x02, 0x00,
					0x00, HAM_POWERON_REQUEST };
		ret = ham_send_and_wait(pdev, ham, fw, loaded, sizeof(loaded),
					false, response, sizeof(response));
		if (ret < 0)
			goto out;
	}

	/* This is the version query used by modem_display_dsp_version(). */
	{
		const u8 version[] = { 0x94, 0x00, 0x00, 0x00 };
		memset(response, 0, sizeof(response));
		ret = ham_send_and_wait(pdev, ham, fw, version, sizeof(version),
					true, response, sizeof(response));
		if (ret >= 5) {
			dev_info(&pdev->dev, "Cutlass DSP version payload: %*ph\n",
				 ret - 4, response + 4);
			ret = 0;
		} else if (ret >= 0) {
			ret = -EPROTO;
		}
	}

	if (survey_speakerphone) {
		const u8 enter[] = { 0xa2, 0x00, 0x02, 0x00, 0x01, 0x00 };
		const u8 leave[] = { 0xa2, 0x00, 0x02, 0x00, 0x00, 0x00 };
		const u8 idle[] = { 0x60, 0x00, 0x02, 0x00, 0x00, 0x00 };

		dev_info(&pdev->dev, "survey: entering speakerphone patch mode\n");
		ret = ham_send_and_wait(pdev, ham, fw, enter, sizeof(enter),
					false, response, sizeof(response));
		if (ret)
			goto out;
		dev_info(&pdev->dev, "survey: leaving speakerphone patch mode\n");
		ret = ham_send_and_wait(pdev, ham, fw, leave, sizeof(leave),
					false, response, sizeof(response));
		if (ret)
			goto out;
		ret = ham_send_and_wait(pdev, ham, fw, idle, sizeof(idle),
					false, response, sizeof(response));
	}
	if (!ret && survey_mode >= 0) {
		u8 connect[] = { 0x68, 0x00, 0x06, 0x00,
				 0x00, 0x00, 0x00, 0x00, 0x00, 0x00 };
		const u8 idle[] = { 0x60, 0x00, 0x02, 0x00, 0x00, 0x00 };

		if (survey_mode > 24) {
			ret = -EINVAL;
			goto out;
		}
		connect[5] = survey_mode;
		dev_info(&pdev->dev,
			 "survey: preparing modulation mode %d while DAA remains on-hook\n",
			 survey_mode);
		ret = ham_send_and_wait(pdev, ham, fw, connect, sizeof(connect),
					false, response, sizeof(response));
		if (!ret)
			ret = ham_send_and_wait(pdev, ham, fw, idle, sizeof(idle),
						false, response, sizeof(response));
	}
	if (!ret && dial_number)
		ret = ham_dial_playground(pdev, ham, fw, dial_number);
	if (!ret && answer_mode >= 0)
		ret = ham_answer_playground(pdev, ham, fw, answer_mode);
	goto out;

malformed:
	dev_err(&pdev->dev, "malformed or unexpected PatchArray at %#zx\n", offset);
out:
	release_firmware(fw);
	return ret;
}

static int ham_probe(struct pci_dev *pdev, const struct pci_device_id *id)
{
	struct ham_probe *ham;
	u16 pci_command;
	int ret;

	pci_read_config_word(pdev, PCI_COMMAND, &pci_command);
	ret = pcim_enable_device(pdev);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "cannot enable PCI device\n");

	if (!(pci_resource_flags(pdev, 0) & IORESOURCE_MEM) ||
	    pci_resource_len(pdev, 0) < 0x100)
		return dev_err_probe(&pdev->dev, -ENODEV,
				     "BAR0 is not the expected MMIO window\n");

	ret = pcim_request_all_regions(pdev, DRV_NAME);
	if (ret)
		return dev_err_probe(&pdev->dev, ret, "cannot claim PCI regions\n");

	ham = devm_kzalloc(&pdev->dev, sizeof(*ham), GFP_KERNEL);
	if (!ham)
		return -ENOMEM;

	ham->bar0 = pcim_iomap(pdev, 0, 0);
	if (!ham->bar0)
		return dev_err_probe(&pdev->dev, -ENOMEM, "cannot map BAR0\n");

	pci_set_drvdata(pdev, ham);
	ham->original_pci_command = pci_command;
	dev_info(&pdev->dev, "BAR0 %pa, length %#llx; reset=%s\n",
		 &pdev->resource[0].start,
		 (unsigned long long)pci_resource_len(pdev, 0),
		 do_reset ? "enabled" : "disabled");
	ham_log_registers(pdev, ham, "before");

	if (do_reset) {
		ret = ham_reset_dsp(pdev, ham);
		ham_log_registers(pdev, ham, "after");
		if (ret)
			goto restore_command;
	}
	if (load_patch) {
		ret = ham_load_poweron_patch(pdev, ham);
		ham_log_registers(pdev, ham, "post-patch");
		if (ret)
			goto restore_command;
	}

	return 0;

restore_command:
	ham_quiesce(pdev, ham);
	pci_write_config_word(pdev, PCI_COMMAND, ham->original_pci_command);
	return ret;
}

static void ham_remove(struct pci_dev *pdev)
{
	struct ham_probe *ham = pci_get_drvdata(pdev);

	ham_quiesce(pdev, ham);
	/* Return PCI decode/master enables to their exact pre-probe state. */
	pci_write_config_word(pdev, PCI_COMMAND, ham->original_pci_command);
	dev_info(&pdev->dev, "probe removed; restored PCI command word %#06x\n",
		 ham->original_pci_command);
}

static const struct pci_device_id ham_ids[] = {
	{ PCI_DEVICE(0x1813, 0x4000) },
	{ }
};
MODULE_DEVICE_TABLE(pci, ham_ids);

static struct pci_driver ham_driver = {
	.name = DRV_NAME,
	.id_table = ham_ids,
	.probe = ham_probe,
	.remove = ham_remove,
};
module_pci_driver(ham_driver);

MODULE_AUTHOR("modem-dsp-emu contributors");
MODULE_DESCRIPTION("Guarded probe for the Ambient/Intel HaM DSP interface");
MODULE_LICENSE("GPL");
