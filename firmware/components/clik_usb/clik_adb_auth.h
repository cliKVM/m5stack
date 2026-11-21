/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include "esp_err.h"
#include <stddef.h>
#include <stdint.h>
esp_err_t clik_adb_auth_init(void);
esp_err_t clik_adb_auth_sign(const uint8_t token[20], uint8_t signature[256]);
const char *clik_adb_auth_public_key(void);
