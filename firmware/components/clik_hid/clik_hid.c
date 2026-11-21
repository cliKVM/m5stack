/* SPDX-License-Identifier: Apache-2.0
 * Device descriptors follow the public ESP-IDF tusb_hid example.
 * This file implements manual input only; no screenshot is obtained by HID.
 */
#include "clik_hid.h"
#include "sdkconfig.h"
#include "tinyusb.h"
#include "class/hid/hid_device.h"
#include "esp_mac.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include <stdio.h>

enum { KEYBOARD = 0, MOUSE = 1 };
#if CFG_TUD_HID != 2
#error "USB HID personality requires CONFIG_TINYUSB_HID_COUNT=2; use a *-hid profile"
#endif
static const uint8_t keyboard_desc[] = {TUD_HID_REPORT_DESC_KEYBOARD()};
static const uint8_t mouse_desc[] = {TUD_HID_REPORT_DESC_MOUSE()};
#if CONFIG_CLIK_HID_SELF_POWERED
#define CONFIG_ATTRIBUTES TUSB_DESC_CONFIG_ATT_SELF_POWERED
#else
#define CONFIG_ATTRIBUTES 0
#endif
static const uint8_t configuration[] = {
    TUD_CONFIG_DESCRIPTOR(1, 2, 0, TUD_CONFIG_DESC_LEN + 2 * TUD_HID_DESC_LEN, CONFIG_ATTRIBUTES,
                          500),
    TUD_HID_DESCRIPTOR(KEYBOARD, 4, HID_ITF_PROTOCOL_KEYBOARD, sizeof keyboard_desc, 0x81, 16, 10),
    TUD_HID_DESCRIPTOR(MOUSE, 5, HID_ITF_PROTOCOL_MOUSE, sizeof mouse_desc, 0x82, 16, 10),
};
static char serial[13];
static const char *strings[] = {(const char[]){0x09, 0x04},
                                "clikVM development",
                                "hey Asa keyboard and mouse",
                                serial,
                                "Keyboard",
                                "Mouse"};
static SemaphoreHandle_t input_lock;
static bool installed, key_held, mouse_held;
static int64_t key_at, mouse_at;

uint8_t const *tud_hid_descriptor_report_cb(uint8_t instance) {
    return instance == KEYBOARD ? keyboard_desc : instance == MOUSE ? mouse_desc : NULL;
}
uint16_t tud_hid_get_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t type,
                               uint8_t *buffer, uint16_t requested) {
    (void)instance;
    (void)report_id;
    (void)type;
    (void)buffer;
    (void)requested;
    return 0;
}
void tud_hid_set_report_cb(uint8_t instance, uint8_t report_id, hid_report_type_t type,
                           uint8_t const *buffer, uint16_t size) {
    /* Keyboard LED output is not used. No received report can execute code. */
    (void)instance;
    (void)report_id;
    (void)type;
    (void)buffer;
    (void)size;
}
bool clik_hid_ready(void) {
    return installed && tud_mounted() && !tud_suspended();
}
static bool wait_ready(unsigned instance, int64_t deadline) {
    while (esp_timer_get_time() < deadline) {
        if (!clik_hid_ready())
            return false;
        if (tud_hid_n_ready(instance))
            return true;
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    return false;
}
static void release_worker(void *unused) {
    (void)unused;
    for (;;) {
        if (xSemaphoreTake(input_lock, pdMS_TO_TICKS(10)) == pdTRUE) {
            int64_t now = esp_timer_get_time();
            if (clik_hid_ready()) {
                if (key_held && now - key_at > 100000 && tud_hid_n_ready(KEYBOARD) &&
                    tud_hid_n_keyboard_report(KEYBOARD, 0, 0, NULL))
                    key_held = false;
                if (mouse_held && now - mouse_at > 2000000 && tud_hid_n_ready(MOUSE) &&
                    tud_hid_n_mouse_report(MOUSE, 0, 0, 0, 0, 0, 0))
                    mouse_held = false;
            }
            xSemaphoreGive(input_lock);
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}
esp_err_t clik_hid_start(void) {
    if (installed)
        return ESP_ERR_INVALID_STATE;
#if CONFIG_CLIK_HID_SELF_POWERED
    if (CONFIG_CLIK_HID_VBUS_GPIO < 0 || CONFIG_CLIK_HID_VBUS_GPIO == 19 ||
        CONFIG_CLIK_HID_VBUS_GPIO == 20)
        return ESP_ERR_INVALID_ARG;
#endif
    input_lock = xSemaphoreCreateMutex();
    if (!input_lock)
        return ESP_ERR_NO_MEM;
    uint8_t mac[6];
    esp_err_t e = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (e != ESP_OK) {
        vSemaphoreDelete(input_lock);
        input_lock = NULL;
        return e;
    }
    snprintf(serial, sizeof serial, "%02X%02X%02X%02X%02X%02X", mac[0], mac[1], mac[2], mac[3],
             mac[4], mac[5]);
    const tinyusb_config_t config = {
        .device_descriptor = NULL,
        .string_descriptor = strings,
        .string_descriptor_count = sizeof strings / sizeof strings[0],
        .external_phy = false,
        .configuration_descriptor = configuration,
#if CONFIG_CLIK_HID_SELF_POWERED
        .self_powered = true,
        .vbus_monitor_io = CONFIG_CLIK_HID_VBUS_GPIO,
#else
        .self_powered = false,
#endif
    };
    e = tinyusb_driver_install(&config);
    if (e != ESP_OK) {
        vSemaphoreDelete(input_lock);
        input_lock = NULL;
        return e;
    }
    installed = true;
    if (xTaskCreate(release_worker, "hid_release", 3072, NULL, 5, NULL) != pdPASS) {
        if (tinyusb_driver_uninstall() == ESP_OK) {
            installed = false;
            vSemaphoreDelete(input_lock);
            input_lock = NULL;
        }
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}
esp_err_t clik_hid_key(uint8_t modifiers, uint8_t usage, uint32_t timeout_ms) {
    if (!clik_hid_ready() || !timeout_ms || usage > 101)
        return ESP_ERR_INVALID_STATE;
    int64_t deadline = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    if (xSemaphoreTake(input_lock, pdMS_TO_TICKS(timeout_ms)) != pdTRUE)
        return ESP_ERR_TIMEOUT;
    uint8_t keys[6] = {usage};
    esp_err_t e = ESP_ERR_TIMEOUT;
    if (wait_ready(KEYBOARD, deadline) && tud_hid_n_keyboard_report(KEYBOARD, 0, modifiers, keys)) {
        key_held = true;
        key_at = esp_timer_get_time();
        if (wait_ready(KEYBOARD, deadline) && tud_hid_n_keyboard_report(KEYBOARD, 0, 0, NULL)) {
            key_held = false;
            e = ESP_OK;
        }
    }
    xSemaphoreGive(input_lock);
    return e;
}
esp_err_t clik_hid_mouse(uint8_t buttons, int8_t x, int8_t y, int8_t wheel, uint32_t timeout_ms) {
    if (!clik_hid_ready() || !timeout_ms || buttons > 7)
        return ESP_ERR_INVALID_STATE;
    int64_t deadline = esp_timer_get_time() + (int64_t)timeout_ms * 1000;
    if (xSemaphoreTake(input_lock, pdMS_TO_TICKS(timeout_ms)) != pdTRUE)
        return ESP_ERR_TIMEOUT;
    esp_err_t e = ESP_ERR_TIMEOUT;
    if (wait_ready(MOUSE, deadline) && tud_hid_n_mouse_report(MOUSE, 0, buttons, x, y, wheel, 0)) {
        mouse_held = buttons != 0;
        mouse_at = esp_timer_get_time();
        e = ESP_OK;
    }
    xSemaphoreGive(input_lock);
    return e;
}
