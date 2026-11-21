/* SPDX-License-Identifier: Apache-2.0 */
#ifndef CLIK_USB_H
#define CLIK_USB_H
#include "esp_err.h"
#include "clik_protocol.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef enum {
    CLIK_USB_DISCONNECTED,
    CLIK_USB_CONNECTED,
    CLIK_USB_AOA_READY,
    CLIK_USB_ADB_AUTHORIZING,
    CLIK_USB_ADB_READY,
    CLIK_USB_ERROR
} clik_usb_state_t;
typedef struct {
    clik_usb_state_t state;
    bool connected, aoa_ready, adb_ready;
    uint16_t vid, pid;
    uint32_t connection_generation;
    esp_err_t last_error;
} clik_usb_status_t;
typedef void (*clik_usb_status_cb)(const clik_usb_status_t *, void *);
typedef esp_err_t (*clik_usb_sink_t)(const uint8_t *data, size_t length, void *context);
typedef struct {
    clik_usb_status_cb on_status; /* Called on USB task. Must return promptly. */
    void *context;
    size_t max_capture_bytes; /* 0 defaults to 16 MiB; forwarded incrementally. */
} clik_usb_config_t;

/* Board code MUST configure host VBUS before start. NVS must be initialized.
 * Owns internal USB PHY. USB Serial/JTAG console must be disabled.
 * Creates daemon and single client task. Does not navigate/approve Android dialogs.
 */
esp_err_t clik_usb_start(const clik_usb_config_t *config);
void clik_usb_get_status(clik_usb_status_t *out);
const char *clik_usb_state_name(clik_usb_state_t state);

/* Blocking calls: queued to the single USB client task; caller is released after
 * operation and cancellation cleanup finish. Never call from on_status/sink.
 * Sink runs on the USB task and may apply bounded network backpressure. Context
 * stays valid until capture returns. The callback receives raw PNG, not ADB frames.
 */
esp_err_t clik_usb_send_key(uint8_t modifier, uint8_t usage, uint32_t timeout_ms);
esp_err_t clik_usb_send_mouse(uint8_t buttons, int8_t dx, int8_t dy,
                            int8_t wheel, uint32_t timeout_ms);
esp_err_t clik_usb_execute(const clik_action_t *action, uint32_t timeout_ms);
esp_err_t clik_usb_capture(clik_usb_sink_t sink, void *context, uint32_t timeout_ms);
#ifdef __cplusplus
}
#endif
#endif
