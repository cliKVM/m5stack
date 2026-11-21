/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "esp_err.h"
#include <stdbool.h>
#include <stdint.h>
esp_err_t clik_hid_start(void);
bool clik_hid_ready(void);
esp_err_t clik_hid_key(uint8_t modifiers, uint8_t usage, uint32_t timeout_ms);
esp_err_t clik_hid_mouse(uint8_t buttons, int8_t x, int8_t y, int8_t wheel, uint32_t timeout_ms);
/* Device role only. This component never enables host VBUS or starts ADB. */
