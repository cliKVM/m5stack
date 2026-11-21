/* SPDX-License-Identifier: Apache-2.0 */
#include "clik_hid.h"
esp_err_t clik_hid_start(void) {
    return ESP_ERR_NOT_SUPPORTED;
}
bool clik_hid_ready(void) {
    return false;
}
esp_err_t clik_hid_key(uint8_t modifiers, uint8_t usage, uint32_t timeout_ms) {
    (void)modifiers;
    (void)usage;
    (void)timeout_ms;
    return ESP_ERR_NOT_SUPPORTED;
}
esp_err_t clik_hid_mouse(uint8_t buttons, int8_t x, int8_t y, int8_t wheel, uint32_t timeout_ms) {
    (void)buttons;
    (void)x;
    (void)y;
    (void)wheel;
    (void)timeout_ms;
    return ESP_ERR_NOT_SUPPORTED;
}
